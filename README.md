# Spotify con TiktokLive (pide tu canción en tus Live´s) 

Aplicación en Python para gestionar solicitudes de canciones desde TikTok Live y enviarlas a Spotify, con cola en memoria, modo configurable en tiempo real y overlay local.

## 1. Función de la aplicación

La aplicación conecta tres piezas:

1. **TikTok Live**: recibe comentarios y regalos.
2. **Spotify Web API**: busca canciones, las encola y permite saltar pista.
3. **Overlay local (Flask)**: muestra "Ahora suena" y la cola de reproducción.

Además, incluye un **panel local de modos** para alternar entre flujo por donación y flujo gratuito.

## 2. Estado funcional actual

Actualmente existen **2 modos operativos**:

1. **`donation`**
   - `!play` requiere regalo.
   - `!skip` requiere regalo.
   - Existe prioridad con regalo específico.
2. **`free`**
   - `!play` libre para cualquier usuario que comente.
   - `!skip` solo host (dueño del live).

El modo activo se guarda en `mode_config.json` y se cambia desde `http://127.0.0.1:5000/panel`.

![MUESTRA:](assets/m1.gif)

## 3. Requisitos

- Python 3.10 o superior.
- Cuenta de Spotify Premium activa.
- Aplicación registrada en Spotify Developer Dashboard (Generalmente ya incluye con la cuenta Spotify Premium).
- Usuario de TikTok Live configurado en `.env`.

## 4. Instalación

Desde la carpeta del proyecto:

```bash
pip install -r requirements.txt
```

Dependencias actuales (según `requirements.txt`):

- `TikTokLive`
- `spotipy`
- `flask`
- `python-dotenv`

## 5. Configuración de Spotify Developer

En tu app de Spotify Developer:

1. Crea (o usa) una aplicación.
2. Configura exactamente este Redirect URL:
   - `http://127.0.0.1:8888/callback`
3. Copia:
   - `Client ID`
   - `Client Secret`

## 6. Variables de entorno (`.env`)

Archivo esperado:

```env
SPOTIFY_CLIENT_ID=tu_client_id
SPOTIFY_CLIENT_SECRET=tu_client_secret
SPOTIFY_REDIRECT_URL=http://127.0.0.1:8888/callback
TIKTOK_USERNAME=tu_usuario_tiktok
SIGN_API_KEY=TU_API_KEY_Eulerstream --> (SOLICITAR GRATIS EN https://www.eulerstream.com)
```

Notas:

- `SIGN_API_KEY` es opcional (servicio de firma, por ejemplo Eulerstream).
- No compartas este archivo ni captures pantalla con sus valores.

## 7. Ejecución

Desde `Spotify-TiktokLive-queue`:
Abre CMD desde dentro de la carpeta donde están todos los archivos de esta APP (No hace falta abrir como Administrador)

```bash
python main.py
```

Al iniciar:

1. Se levanta Flask en `127.0.0.1:5000`.
2. Se solicita autenticación de Spotify en navegador.
3. Se inicia el listener de TikTok con reconexión automática.

## 8. Endpoints locales

- `GET /` → Overlay principal.
- `GET /state` → Estado JSON (cola, ahora suena, modo).
- `GET/POST /panel` → Panel para cambiar modo.
- `GET /health` → Respuesta simple: `OK`.

## 8.1 Integración con TikTok Live Studio

Para usar este overlay sin capturar toda la pantalla, necesitas agregar una fuente web en Live Studio.

Puntos importantes:

1. La app se sirve en local (`http://127.0.0.1:5000`).
2. Live Studio rechazan URLs locales o `http` y exigen `https` público.
3. Si aparece `URL inválida`, no es un fallo del bot: es una restricción del cliente de Live Studio.

![MUESTRA:](assets/m2.gif)

Opción práctica:

1. Mantener uso local con captura de ventana (sin exponer internet).

## 9. Modos y mapeo de regalos (coins)

### 9.1 Modo `donation`

Reglas activas:

- Solo se habilitan ventanas de `!play` y `!skip` al recibir regalos.
- El mapeo de acción intenta primero por valor de monedas y, si no está disponible, por nombre del regalo.

Mapeo actual por monedas (`GIFT_COIN_ACTIONS`):

- `1` → `play`
- `5` → `skip`
- `30` → `priority`

Mapeo por nombre (`GIFT_NAME_ACTIONS`) como respaldo:

- `play`: rosa / rose
- `skip`: corazón coreano / korean heart
- `priority`: rosquilla / donut / doughnut

### 9.2 Modo `free`

Reglas activas:

- `!play` funciona sin regalo.
- `!skip` solo funciona para el host (`TIKTOK_USERNAME`).

## 10. Panel de modos

Panel disponible en:

- `http://127.0.0.1:5000/panel`

Opciones actuales:

1. **Modo 1 - Donación (regalos)**
2. **Modo 2 - Gratis para todos**

El cambio se aplica al momento y se persiste en `mode_config.json`.

## 11. Reglas de comandos y validaciones

### 11.1 Formato de `!play`

Formato válido:

```text
!play Canción - Artista
```

Detalles:

- Se toleran guiones normales y guiones largos.
- Si el formato es inválido, se registra como intento inválido.

### 11.2 Ventanas y límites anti-spam

Configuración activa en `main.py` y `anti_spam.py`:

- Ventana de `!play`: **180 s**.
- Ventana de `!skip`: **60 s**.
- Cooldown por usuario: **10 s**.
- Rate limit global: **5 comandos cada 10 s**.
- Máximo de canciones en cola por usuario: **2**.
- Intentos de `!play`: **3**.
- Formato inválido: los 2 primeros no consumen intento; desde el 3º sí consume.

### 11.3 Validaciones de las canciones

- Duración máxima permitida: **5 minutos** (`MAX_DURATION_MS`).
- Canciones explícitas: **permitidas**.
- Bloqueo de duplicados por **20 minutos** (cola actual + recientes), salvo prioridad.

## 12. Búsqueda y reproducción en Spotify

### 12.1 Búsqueda

Se usa búsqueda precisa con umbrales actuales:

- Coincidencia mínima de título: `0.58`
- Coincidencia mínima de artista: `0.50`

Si falla la búsqueda precisa, hay fallback a búsqueda amplia por popularidad.

### 12.2 Reintentos ante errores transitorios

Para errores de red/servicio (429/5xx/timeouts), Spotify usa reintentos con backoff:

- 5 s
- 15 s
- 60 s

Durante esos casos se registra en log:

- `Spotify no responde, reintentando...`

## 13. Cola y prioridad

- Cola en memoria con dos niveles: prioritaria y normal.
- En overlay se muestran **máximo 5 elementos**.
- Si hay más, aparece `+N más en cola`.
- Cuando inicia una canción (detectada por polling), se elimina de la cola local por URL.

## 14. Overlay actual

Características actuales del overlay:

- Diseño horizontal: bloque "Ahora suena" + "Lista de Reproducción".
- Muestra portada del álbum si Spotify devuelve `cover_url`.
- Polling de estado cada **5 segundos** (`/state`).
- Fondo visual definido por `overlay/static/style.css`.

## 15. Seguridad de la información

### 15.1 Credenciales

- Nunca publiques `.env`.
- Nunca compartas `Client Secret`, tokens o API keys.
- Evita subir capturas de consola con enlaces de autorización activos.

### 15.2 Exposición de la app

- Flask corre en `127.0.0.1` (solo local).
- Recomendado: mantenerlo local si no necesitas exponer overlay fuera del equipo.
- Si usas túneles públicos, asume mayor superficie de riesgo.

### 15.3 Panel local

- `/panel` no tiene autenticación.
- Es seguro solo en contexto local (`127.0.0.1`).
- No se recomienda exponer el panel directamente a internet.

### 15.4 Buenas prácticas operativas

- Rotar credenciales solo cuando sea necesario (cambio de app, revocación o sospecha de filtración).
- Actualizar dependencias de forma controlada y probar flujo completo después.
- Revisar logs periódicamente para detectar desconexiones recurrentes.

## 16. Logs y diagnóstico rápido

Casos comunes:

1. `TikTok user is offline`:
   - El usuario no está en directo o TikTok no confirma sala activa.
2. `Failed to get current playback`:
   - Spotify no respondió temporalmente (red, timeout o reset).
3. `No Spotify match for ...`:
   - No hubo coincidencia con los umbrales de búsqueda.

El listener de TikTok incluye reconexión con backoff para minimizar caídas.

## 17. Pruebas

El proyecto incluye pruebas unitarias en `tests/` para anti-spam, parser, mapeo de regalos y cola.

Si deseas ejecutarlas:

```bash
python -m pytest -q
```

Si `pytest` no está instalado en tu entorno, instálalo primero.

