# MercadoLibre API — CRAFTERS

Acceso por API a la cuenta de MercadoLibre de CRAFTERS (Argentina / MLA), para
poder pedir análisis desde la Terminal y después construir herramientas encima.

Mismo espíritu que `_exploracion-api-contabilium`: primero mapeamos qué da la
API de verdad, después construimos.

---

## Setup (se hace una sola vez)

### 1. Crear la aplicación en el DevCenter

Entrá a **https://developers.mercadolibre.com.ar/devcenter** logueado con la
cuenta de MercadoLibre de CRAFTERS.

> Importante: tiene que ser el **usuario administrador** de la cuenta, no un
> colaborador/operador. Si entrás con un colaborador, ML rechaza la
> autorización con el error `invalid_operator_user_id`.

Creá una aplicación nueva y completá:

| Campo | Qué poner |
|---|---|
| Nombre | `CRAFTERS Analytics` (o lo que quieras) |
| Descripción | Uso interno: análisis de ventas y publicaciones |
| **Redirect URI** | `https://www.crafters.com.ar` |
| **Flujos Oauth** | tildar `Authorization Code` **y `Refresh Token`** |
| Requiere PKCE | dejar **destildado** (el código está preparado así) |
| Negocios | tildar **`Mercado Libre`** (VIS no aplica) |
| Permisos | acceso de **lectura** a órdenes, ítems y métricas |
| Notificaciones (webhooks) | dejar vacío por ahora |

Dos trampas del panel:

- **`Refresh Token` viene destildado por defecto.** Es el equivalente al scope
  `offline_access`. Si no lo tildás, el acceso se muere a las 6 horas y hay que
  reautorizar a mano cada vez. **Es el checkbox más importante de la pantalla.**
- El **Redirect URI no acepta `localhost`** (tira "La dirección debe ser
  válida"): ML exige un dominio público real. Por eso usamos el de Crafters.
  No hace falta que la página haga nada — el `code` viaja en la dirección y lo
  copiás de la barra del navegador.

Al guardar te va a mostrar el **App ID** y la **Secret Key**. La Secret Key se
muestra una sola vez — copiala.

### 2. Cargar las credenciales

Copiá `credentials.txt.example` como `credentials.txt` y completá los tres
valores. El `redirect_uri` tiene que ser **idéntico** al del panel (una barra de
más y falla).

### 3. Autorizar

```bash
python autorizar.py
```

Te va a dar un link, lo abrís en el navegador, das "Permitir", y pegás de vuelta
la dirección a la que te redirigió. **Es normal que esa página tire error de
"no se puede acceder al sitio"** — lo único que importa es la dirección.

Listo. A partir de acá el token se renueva solo.

### 4. Verificar

```bash
python explorar.py
```

Prueba todos los endpoints que nos interesan y reporta cuáles andan. Deja el
detalle en `exploracion.json`.

---

## Cómo funciona la autenticación

MercadoLibre usa OAuth 2.0, que es más vueltero que Contabilium:

- El `access_token` **dura 6 horas**.
- El `refresh_token` dura **6 meses** y es de **un solo uso**: cada renovación
  devuelve uno nuevo y mata al anterior.
- `meli.py` guarda ambos en `tokens.json` y renueva solo cuando faltan menos de
  10 minutos para el vencimiento.

Cosas que **invalidan** los tokens y obligan a correr `autorizar.py` de nuevo:

- Cambiar la contraseña de la cuenta de MercadoLibre.
- Regenerar la Secret Key de la app.
- Revocarle los permisos a la app desde el perfil de ML.
- **No usar la API durante 4 meses seguidos.**
- Perder el `tokens.json` (por eso conviene no borrarlo a mano).

---

## Uso desde la Terminal

```python
from meli import Meli

ml = Meli()

# Datos de la cuenta
ml.get("/users/me")

# Órdenes de un período
ml.get("/orders/search", seller=ml.user_id, sort="date_desc",
       **{"order.date_created.from": "2026-07-01T00:00:00.000-00:00"})

# Todas las publicaciones (usa scroll, pasa el límite de 1000)
ids = list(ml.scan_items())

# Detalle en lote (de a 20 por llamada, lo hace solo)
for item in ml.items_detalle(ids, atributos=["id", "title", "price",
                                             "available_quantity", "sold_quantity"]):
    print(item["title"], item["price"])
```

`ml.get()` maneja solo el rate limit (429) y la renovación de token.

---

## Archivos

| Archivo | Qué hace |
|---|---|
| `meli.py` | Cliente: OAuth, renovación automática, paginado, rate limit |
| `autorizar.py` | Autorización inicial (una sola vez) |
| `explorar.py` | Prueba endpoints contra la cuenta real y reporta qué anda |
| `reporte.py` | Reporte semanal: período contra el anterior + qué resolver |
| `alertas_stock.py` | Días de cobertura por SKU y plata semanal en riesgo |
| `reclamos.py` | Reclamos por producto y tasa sobre unidades vendidas |
| `full.py` | Candidatos a Full por plata de envío que queman |
| `buybox.py` | Buy Box del catálogo: quién gana cada página y a qué precio |
| `promociones.py` | Campañas que ML ofrece por publicación, con su aporte |
| `credentials.txt` | App ID + Secret + Redirect URI (**no se sube a git**) |
| `tokens.json` | Tokens vivos (**no se sube a git**) |

Cada uno corre también suelto desde la Terminal: `python reporte.py`,
`python alertas_stock.py 60`, `python reclamos.py 90`, `python full.py`,
`python buybox.py 150`, `python promociones.py 300`.

---

## Endpoints validados contra la cuenta real (28/07/2026)

Cuenta: `CRAFTERSARG` — user_id `422682314` — site MLA — reputación 5_green.

| Qué | Endpoint | Notas |
|---|---|---|
| Datos del vendedor | `/users/me` | |
| Listado de publicaciones | `/users/{uid}/items/search` | `status=active` / `paused`. Offset topa en 1000 → usar `ml.scan_items()` |
| Detalle de publicación | `/items/{id}` | 61 campos |
| Detalle en lote | `/items?ids=...` | máx. 20 por llamada |
| Comisiones por tipo | `/sites/MLA/listing_prices?price=N` | cuánto cobra ML según exposición |
| Órdenes | `/orders/search?seller={uid}` | fecha en **ISO completo** |
| Detalle de orden | `/orders/{id}` | **`order_items[].sale_fee` = comisión de ML** |
| Envíos | `/shipments/{id}` y `/shipments/{id}/costs` | |
| Facturación de ML | `/billing/integration/monthly/periods` | **requiere `group=ML` y `document_type=BILL`**, si no tira 422 |
| Visitas del vendedor | `/users/{uid}/items_visits` | **fecha simple `YYYY-MM-DD`** |
| Visitas por publicación | `/items/{id}/visits/time_window?last=30&unit=day` | |
| Preguntas | `/questions/search?seller_id={uid}` | `status=UNANSWERED` |
| Reclamos | `/post-purchase/v1/claims/search` | **exige al menos un filtro** y solo ordena con `sort=date_desc` |
| Motivo de reclamo | `/post-purchase/v1/claims/reasons/{id}` | traduce `PNR3210` a texto |
| Buy Box | `/items/{id}/price_to_win?version=v2` | precio para ganar, ganador actual y palancas sin usar |
| Producto de catálogo | `/products/{id}` y `/products/{id}/items` | todos los que venden ese producto |
| Promos de la cuenta | `/seller-promotions/users/{uid}?app_version=v2` | campañas abiertas |
| Promos por publicación | `/seller-promotions/items/{id}?app_version=v2` | ofertas `candidate` y en curso |
| Reputación | `/users/{uid}` → `seller_reputation` | métricas de 60 días de ML |

**Trampa de formatos de fecha:** conviven dos formatos y no son intercambiables.
`/orders/search` quiere ISO completo (`2026-07-01T00:00:00.000-00:00`); los de
visitas quieren `YYYY-MM-DD` pelado y tiran 400 con ISO.

### Trampas de reclamos (validadas 30/07/2026)

Este endpoint tiene tres formas de mentirte **sin dar error**, y las tres se
verificaron contra la cuenta:

- **El filtro de fecha se ignora.** `date_created.from` devuelve exactamente el
  mismo total que sin él (18.117 reclamos históricos). La única forma de acotar
  el período es traer ordenado y cortar por fecha en el cliente.
- **Solo `sort=date_desc` ordena.** `date_created_desc`, `-date_created`,
  `sort_by`/`sort_order` no dan error: se ignoran y devuelven del más viejo al
  más nuevo. Con uno de esos se traen reclamos de 2019 creyendo que son de esta
  semana.
- **El reclamo no trae el producto.** Apunta a un `resource` que puede ser
  `order` (directo), `shipment` (una llamada más a `/shipments/{id}` para sacar
  el `order_id`) o `payment`, que **no tiene camino público al pedido** — el
  filtro `payment_id` de `/orders/search` también se ignora y devuelve las
  40.730 órdenes.

`/post-purchase/v1/claims/reasons/{id}` devuelve el código **canónico**, que
puede ser distinto del pedido (`PNR3210` → `PNR9502`). Es el mismo motivo
renumerado, no un error.

### Buy Box — cómo leer `price_to_win`

`price_to_win` **casi nunca coincide con el precio del ganador**, y suele ser
bastante más bajo. No es un error de la API: ML pondera precio y beneficios
juntos (Full, envío gratis, cuotas). Si el ganador los tiene y vos no, para
empatarle tenés que compensar con precio.

**La diferencia `winner.price - price_to_win` es, en pesos, lo que cuesta no
tener esas palancas.** Sobre las 150 publicaciones de catálogo que más venden,
la mediana de esa penalización es **$2.074**.

Hay casos donde `current_price` ya es **menor** que `winner.price` y el estado
sigue siendo `competing`. Ahí bajar el precio no sirve: lo que falta son los
beneficios. `buybox.py` los marca aparte como *perdés estando más barato*.

`version=v2` cambia la forma de `boosts`: en v1 es un dict de booleanos, en v2
una lista de `{id, status, description}` donde `status` es `boosted` (la usás) u
`opportunity` (está disponible y no la usás). Se usa v2 por el texto legible.

### Dos tasas de reclamo distintas — no compararlas

`reclamos.py` da **2,80%** y la reputación de ML dice **0,19%**. Las dos están
bien: miden cosas distintas.

- La de `reclamos.py` cuenta **todos** los tipos (`cancel_purchase`,
  `mediations`, `returns`) sobre las unidades vendidas del período. Sirve para
  comparar productos entre sí.
- La de ML (`seller_reputation.metrics.claims.rate`) cuenta solo los reclamos
  en sentido estricto sobre las ventas de 60 días, y es la que afecta la
  reputación. La cuenta está en 5_green / platinum.

Lo que **no** anda (no bloquea nada):

- `/sites/MLA/search?seller_id=` y `?nickname=` → **403**. ML cerró la búsqueda
  pública por vendedor. Se reemplaza con `/users/{uid}/items/search`.
- `/billing/integration/periods/key/{key}/group/ML/documents` → 404, la subruta
  de documentos cambió. Los montos por período igual salen de `monthly/periods`.
- `/orders/{id}/discounts` da 404 cuando esa orden no tuvo descuentos: es
  respuesta normal, no una falla.
- `/items/{id}/health`, `/quality/v1/items/{id}` y `/items/{id}/moderations`
  → 404. No hay score de calidad de publicación por API.
- `/sites/MLA/search?q=` → **403** también para búsqueda por texto: el buscador
  público está cerrado del todo, no solo el filtro por vendedor.
- **No hay endpoint de recomendación de Full.** Se probaron siete rutas
  plausibles (`/users/{uid}/stock/fulfillment`,
  `/users/{uid}/items/fulfillment_recommendations`, `/fbm/recommendations`,
  `/sites/MLA/inventory_recommendations` y variantes): todas 404 o 403. Por eso
  `full.py` no estima ahorro, ordena por tamaño del premio.

## Las herramientas

```bash
streamlit run crafters_app.py
```

Once secciones:

| Sección | Qué hace | ¿Escribe en ML? |
|---|---|---|
| **Reporte semanal** | La pantalla del lunes: cómo vino la semana contra la anterior y qué hay que resolver | no |
| **Preguntas** | Respuestas a compradores con IA. Destacada en naranja en el selector | **sí** (publica respuestas) |
| **Alertas** | Stock por agotarse y reclamos por producto | no |
| **Ganar la venta** | Buy Box del catálogo y promociones disponibles | no |
| **Precios** | Cambio masivo de precios desde planilla | **sí** |
| **Mayoristas** | Precios por cantidad según reglas | **sí** |
| **Stock ML** | Cambio masivo de stock desde planilla | **sí** |
| **Control de stock** | Registro propio de unidades, con historial | no (registro propio) |
| **Rentabilidad** | Margen por SKU con cargos reales | no |
| **Competencia** | Mejor precio por EAN | no |
| **Oportunidades** | Siete análisis de plata sobre la mesa | no |

El resaltado naranja de **Preguntas** se hace por CSS con `nth-of-type(2)` sobre
`[data-testid="stButtonGroup"]`: **si se reordena la lista de secciones, hay que
mover el selector junto con ella.**

Precios y stock siguen siempre el mismo flujo, sin atajos:
subir planilla → **simular** → revisar → confirmar → aplicar.
Todo cambio aplicado queda en `auditoria.csv` con el valor anterior.

### Reglas de resolución SKU → publicación

El SKU que manda es el atributo **SELLER_SKU**. Un SKU puede tener varias
publicaciones (el catálogo tiene muchos espejos), así que:

- **Precio**: si entre las publicaciones del SKU conviven Premium (`gold_pro`) y
  Clásica (`gold_special`), se actualizan **solo las Clásicas**. Si son todas del
  mismo tipo, se actualizan todas.
- **Stock**: se agrupa por `user_product_id`. Si el SKU tiene **varios**, no se
  toca y se reporta: poner el mismo número en cada uno duplicaría el stock.
  Las publicaciones en Full quedan siempre afuera.

### Escritura — verificado contra la cuenta real (28/07/2026)

- `PUT /items/{id}` con `{"price": N}` y con `{"available_quantity": N}` funciona.
- **El stock se propaga solo** a todas las publicaciones que comparten
  `user_product_id`: se actualiza una y las demás se mueven solas. Por eso
  `resolver_stock()` devuelve una sola publicación por grupo — no es un
  descuido.
- Probado sobre publicaciones pausadas y revertido al valor original.

### Rentabilidad

Los cargos no se estiman de una tabla: se promedian de lo que ML **efectivamente
cobró** en cada venta histórica de ese SKU (`sale_fee` por unidad + envío que
pagó CRAFTERS, prorrateado por unidades cuando la orden tiene varios SKU).

Los costos de envío se **muestrean** (5 ventas por SKU por defecto) porque es una
llamada por envío. Las ventas sin dato de envío se excluyen del promedio en vez
de contarse como cero, así el costo no queda subestimado; la columna
`cobertura_envio` dice qué proporción tiene dato real.

## Control de stock

Registro propio de unidades, **paralelo al de MercadoLibre** (no lo modifica).
Vive en `stock_control.py` y en las hojas `stock_inicial`, `movimientos` y
`devoluciones` de la Google Sheet.

Reglas:

- La unidad se descuenta **al pagarse la orden**. Si después se cancela, el
  movimiento se revierte solo.
- Las **devoluciones no vuelven solas**: quedan en una bandeja hasta que
  alguien confirme que la unidad está apta para revenderse.
- Compras y ajustes los carga el operador.

**Es idempotente**, y eso es lo que permite correrlo cada 15 minutos: cada
movimiento lleva una clave derivada de la orden y la publicación
(`v:{order_id}:{item_id}`), así que reprocesar el mismo período no duplica
nada. Verificado: correr dos veces sobre el mismo rango agrega 0 movimientos.

La ventana por defecto son varios días hacia atrás, no solo lo nuevo, para
que las **cancelaciones tardías** se enteren.

### Sincronización automática

`.github/workflows/sincronizar_stock.yml` corre cada 15 minutos, de 8 a 21 hs
de Argentina, de lunes a sábado.

> **Por qué no 24/7:** en un repo privado el plan gratuito da 2000 minutos de
> Actions al mes. Cada 15 minutos todo el día serían ~3450 y se cortaría a
> mitad de mes. Esta ventana usa ~950 (cada corrida tarda ~42 s). No se pierde
> ninguna venta: lo de la madrugada o el domingo entra en la primera corrida
> siguiente.

Requiere el secret **`CRAFTERS_SECRETS_TOML`** en el repositorio, con el mismo
contenido que los secrets de Streamlit Cloud (service account **inline**, no
como ruta a un archivo, porque el `sa.json` no está en el repo).

## Deploy en Streamlit Cloud

### Por qué hace falta la Google Sheet

El disco de Streamlit Cloud **se borra en cada reinicio**. Dos cosas no pueden
vivir ahí:

1. **El token de ML.** El `refresh_token` es de un solo uso y rota en cada
   renovación. Si se pierde el último, el anterior ya está invalidado y hay que
   correr `autorizar.py` a mano desde una computadora.
2. **La auditoría.** Es el único registro de quién cambió qué precio.

Por eso `almacen.py` los guarda en una Google Sheet. Sin Sheet configurada la
app funciona igual pero avisa con un cartel, y solo conviene usarla local.

### Pasos

1. **Crear la Google Sheet** (una nueva, vacía). El ID es lo que va entre
   `/d/` y `/edit` en la URL. La app crea sola las hojas `tokens_ml` y
   `auditoria`.
2. **Service account de Google**: en Google Cloud, crear uno con la API de
   Sheets habilitada y bajar el JSON. **Compartir la Sheet como Editor con el
   `client_email` del service account** — si no, no puede escribir.
3. **Subir el repo** a GitHub (privado).
4. En **share.streamlit.io**, apuntar la app a `crafters_app.py`.
5. Cargar los **Secrets** siguiendo `secrets.toml.example`: la contraseña, la
   sección `[mercadolibre]` y la sección `[gsheets]` con el JSON del service
   account pegado inline (en la nube va embebido, nunca como ruta a un archivo).
6. **Autorizar una vez**: correr `python autorizar.py` en local **con el
   `.streamlit/secrets.toml` apuntando a la misma Sheet**. El token queda
   guardado ahí y la app en la nube lo levanta.

### Limitación conocida

Google Sheets no tiene bloqueo de escritura. Si dos personas usan la app al
mismo tiempo y las dos renuevan el token justo en el mismo momento, una puede
invalidar a la otra y habría que reautorizar. Con un solo operador no pasa.

## Qué sigue

Las tres prioridades originales (ventas y facturación, publicaciones y precios,
visitas y conversión) están cubiertas por las diez secciones.

Lo que queda pendiente:

- **Que el reporte semanal llegue solo.** Hoy hay que abrir la app y apretar un
  botón. El objetivo era que llegara sin que nadie se acuerde: un mail los lunes
  a la mañana. La infraestructura ya existe — `reporte.py` corre solo desde la
  Terminal y hay dos GitHub Actions andando (`sincronizar_stock.yml` y
  `monitor_competencia.yml`) que se pueden copiar.
- **Una cuenta fina de Full.** `full.py` ordena por tamaño del premio pero no
  estima ahorro, porque con 20 SKU en Full no hay muestra. Si CRAFTERS manda más
  productos, el mismo módulo va a poder comparar: la columna `comparable` de la
  tabla por franja avisa cuándo se llega a los 15 SKU de cada lado.
- **Los ~585 reclamos más viejos** no son accesibles por API (el listado de
  preguntas y reclamos topa en los más recientes). Solo se pueden sacar
  exportando desde el panel de ML.
