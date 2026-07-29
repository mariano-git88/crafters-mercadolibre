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
| `credentials.txt` | App ID + Secret + Redirect URI (**no se sube a git**) |
| `tokens.json` | Tokens vivos (**no se sube a git**) |

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

**Trampa de formatos de fecha:** conviven dos formatos y no son intercambiables.
`/orders/search` quiere ISO completo (`2026-07-01T00:00:00.000-00:00`); los de
visitas quieren `YYYY-MM-DD` pelado y tiran 400 con ISO.

Lo que **no** anda (no bloquea nada):

- `/sites/MLA/search?seller_id=` y `?nickname=` → **403**. ML cerró la búsqueda
  pública por vendedor. Se reemplaza con `/users/{uid}/items/search`.
- `/billing/integration/periods/key/{key}/group/ML/documents` → 404, la subruta
  de documentos cambió. Los montos por período igual salen de `monthly/periods`.
- `/orders/{id}/discounts` da 404 cuando esa orden no tuvo descuentos: es
  respuesta normal, no una falla.

## Las herramientas

```bash
streamlit run crafters_app.py
```

Tres secciones: **Precios**, **Stock** y **Rentabilidad**.

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

Prioridades definidas para las herramientas:

1. **Ventas y facturación** — órdenes, unidades, facturación por período y por
   publicación, comisiones de ML (`sale_fee`) y costos de envío.
2. **Publicaciones y precios** — catálogo, precios, stock, pausadas, sin stock.
3. **Visitas y conversión** — qué se ve mucho y no vende.
