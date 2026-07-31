#!/usr/bin/env python3
"""
Envio de mails. Lo usan los reportes automaticos.

    python correo.py            -> muestra como esta configurado
    python correo.py --prueba   -> manda un mail de prueba

**La configuracion vive en los secrets**, en una seccion `[correo]`:

    [correo]
    proveedor    = "resend"                  # resend | smtp
    api_key      = "re_..."                  # solo para resend
    remitente    = "reportes@crafters.com.ar"
    destinatarios = "mariano@crafters.com.ar, otro@crafters.com.ar"

    # si proveedor = "smtp"
    smtp_host    = "smtp.gmail.com"
    smtp_puerto  = 587
    smtp_usuario = "..."
    smtp_clave   = "..."                     # clave de aplicacion, no la real

Sin `[correo]` configurado **no falla**: `enviar()` devuelve (False, motivo) y
el que llama decide. Los cron guardan el reporte igual y lo dejan en el log,
asi que se puede probar todo el circuito antes de tener el mail andando.
"""

import smtplib
import sys
from email.message import EmailMessage

import almacen


def config():
    """La seccion [correo] de los secrets, o {} si no esta."""
    try:
        cfg = almacen._seccion("correo")
    except Exception:                          # noqa: BLE001
        cfg = None
    return dict(cfg) if cfg else {}


def destinatarios(cfg=None):
    cfg = cfg if cfg is not None else config()
    crudo = str(cfg.get("destinatarios", ""))
    return [d.strip() for d in crudo.replace(";", ",").split(",") if d.strip()]


def configurado():
    cfg = config()
    if not cfg or not destinatarios(cfg):
        return False
    if str(cfg.get("proveedor", "resend")).lower() == "resend":
        return bool(cfg.get("api_key") and cfg.get("remitente"))
    return bool(cfg.get("smtp_host") and cfg.get("smtp_usuario")
                and cfg.get("smtp_clave"))


def _por_resend(cfg, asunto, html, para):
    import requests

    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {cfg['api_key']}",
                 "Content-Type": "application/json"},
        json={"from": cfg["remitente"], "to": para,
              "subject": asunto, "html": html},
        timeout=30)
    if r.status_code >= 300:
        return False, f"Resend HTTP {r.status_code}: {r.text[:300]}"
    return True, r.json().get("id", "")


def _por_smtp(cfg, asunto, html, para):
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = cfg.get("remitente") or cfg["smtp_usuario"]
    msg["To"] = ", ".join(para)
    msg.set_content("Este reporte se ve mejor en un lector con HTML.")
    msg.add_alternative(html, subtype="html")

    puerto = int(cfg.get("smtp_puerto", 587))
    with smtplib.SMTP(cfg["smtp_host"], puerto, timeout=30) as s:
        s.starttls()
        s.login(cfg["smtp_usuario"], cfg["smtp_clave"])
        s.send_message(msg)
    return True, "enviado"


def enviar(asunto, html, para=None):
    """
    Manda el mail. Devuelve (ok, detalle).

    **Nunca lanza**: un reporte que no se pudo mandar no puede tumbar el
    proceso que lo genero. El detalle explica que falto.
    """
    cfg = config()
    if not cfg:
        return False, ("No hay sección [correo] en los secrets. El reporte se "
                       "generó igual.")

    para = para or destinatarios(cfg)
    if not para:
        return False, "No hay destinatarios configurados en [correo]."

    proveedor = str(cfg.get("proveedor", "resend")).lower()
    try:
        if proveedor == "resend":
            if not cfg.get("api_key"):
                return False, "Falta api_key para Resend."
            return _por_resend(cfg, asunto, html, para)
        if proveedor == "smtp":
            faltan = [c for c in ("smtp_host", "smtp_usuario", "smtp_clave")
                      if not cfg.get(c)]
            if faltan:
                return False, f"Faltan en [correo]: {', '.join(faltan)}"
            return _por_smtp(cfg, asunto, html, para)
        return False, f"Proveedor desconocido: {proveedor}"
    except Exception as e:                     # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:250]}"


def main():
    cfg = config()
    if not cfg:
        print("Sin sección [correo] en los secrets. Ver el docstring de "
              "este archivo para el formato.")
        return 1

    print(f"proveedor:     {cfg.get('proveedor', 'resend')}")
    print(f"remitente:     {cfg.get('remitente', '—')}")
    print(f"destinatarios: {', '.join(destinatarios(cfg)) or '—'}")
    print(f"configurado:   {'sí' if configurado() else 'NO'}")

    if "--prueba" in sys.argv:
        ok, detalle = enviar(
            "Prueba — Herramientas de MercadoLibre CRAFTERS",
            "<p>Si estás leyendo esto, el envío automático de reportes "
            "funciona.</p>")
        print(f"\nprueba: {'OK' if ok else 'FALLÓ'} — {detalle}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
