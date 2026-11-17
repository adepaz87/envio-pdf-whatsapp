"""
Servicio independiente: envía un PDF por WhatsApp (Twilio) como archivo, sin texto.
No comparte código ni base de datos con el bot. Guarda los PDF solo en memoria
unas horas, lo justo para que Twilio los descargue.

Variables (Railway): TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WA_NUMBER,
INFORME_PDF_TOKEN. Opcional: DESTINO (por defecto +34690875940).

POST /enviar        cabecera X-Api-Token; JSON {"pdf_base64": "...", "nombre": "x.pdf"}
GET  /estado/<sid>  cabecera X-Api-Token; estado de entrega en Twilio
GET  /pdf/<clave>/<nombre>  sirve el PDF a Twilio (clave aleatoria)
GET  /health
"""
import base64, hmac, os, re, secrets, threading, time
from flask import Flask, Response, request

app = Flask(__name__)
_PDFS = {}               # clave -> (timestamp, nombre, bytes)
_LOCK = threading.Lock()
RETENCION = 6 * 3600
MAX_BYTES = 10 * 1024 * 1024


def _auth():
    token = os.environ.get('INFORME_PDF_TOKEN', '')
    if not token:
        return {'error': 'falta INFORME_PDF_TOKEN'}, 503
    if not hmac.compare_digest(request.headers.get('X-Api-Token', '').encode(), token.encode()):
        return {'error': 'no autorizado'}, 401
    return None


def _twilio():
    from twilio.rest import Client
    return Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/enviar')
def enviar():
    err = _auth()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        pdf = base64.b64decode(str(data.get('pdf_base64', '')))
    except Exception:
        return {'error': 'pdf_base64 no válido'}, 400
    if not pdf.startswith(b'%PDF'):
        return {'error': 'el archivo no es un PDF'}, 400
    if len(pdf) > MAX_BYTES:
        return {'error': 'PDF demasiado grande'}, 413
    nombre = re.sub(r'[^A-Za-z0-9._-]', '_', str(data.get('nombre') or 'informe.pdf'))[:100]
    if not nombre.lower().endswith('.pdf'):
        nombre += '.pdf'
    clave = secrets.token_urlsafe(24)
    ahora = time.time()
    with _LOCK:
        for k in [k for k, v in _PDFS.items() if ahora - v[0] > RETENCION]:
            _PDFS.pop(k, None)
        _PDFS[clave] = (ahora, nombre, pdf)
    dominio = os.environ.get('RAILWAY_PUBLIC_DOMAIN') or request.host
    media_url = f'https://{dominio}/pdf/{clave}/{nombre}'
    destino = os.environ.get('DESTINO', '+34690875940')
    try:
        msg = _twilio().messages.create(
            from_=os.environ.get('TWILIO_WA_NUMBER', ''),
            to=f'whatsapp:{destino}',
            media_url=[media_url])
    except Exception as e:
        return {'error': f'Twilio: {e}', 'code': getattr(e, 'code', None)}, 502
    return {'status': str(msg.status), 'sid': msg.sid}, 200


@app.get('/estado/<sid>')
def estado(sid):
    err = _auth()
    if err:
        return err
    try:
        m = _twilio().messages(sid).fetch()
    except Exception as e:
        return {'error': str(e)}, 502
    return {'status': str(m.status), 'error_code': m.error_code, 'error_message': m.error_message}


@app.get('/pdf/<clave>/<nombre>')
def servir(clave, nombre):
    with _LOCK:
        item = _PDFS.get(clave)
    if not item:
        return 'No encontrado', 404
    return Response(item[2], mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{item[1]}"'})
