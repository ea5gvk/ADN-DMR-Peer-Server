# DMR Tier III — protocolo Homebrew DMR trunking v0.6

## Función

El **protocolo Homebrew DMR trunking v0.6** (DL5ABM / YO8RZZ) une un **emplazamiento DMR Tier III** con un servidor de red. El emplazamiento es dmrtc detrás de DMRGateway con `TrunkingEnabled=1`. Es HBP con tres cambios:

1. todos los opcodes de login y keepalive cambian de nombre (`DTC*`);
2. las tramas `DMRD` llevan un **UUID de llamada** de 16 bytes (71 bytes);
3. un mensaje nuevo, **`DMRT`**, lleva la señalización: registros de radios, suscripciones de talkgroups, datos cortos UDT, establecimiento de llamada privada y estado.

El servidor lo habla en cada MASTER con `TRUNKING: true`, y solo ahí. Puesta en marcha y comportamiento para operadores: [Trunking DMR Tier III](../user-guide/tier3-trunking.md).

## Login y keepalive

Formatos y longitudes son los de su gemelo HBP clásico; solo cambia el nombre.

| El emplazamiento envía | Longitud | El servidor responde | Gemelo clásico |
|------------------------|----------|----------------------|----------------|
| `DTCL` + ID de emplazamiento (4) | 8 | `DTCACK` + salt (4) | `RPTL` |
| `DTCK` + ID + SHA-256(salt ‖ contraseña) | 40 | `DTCACK` + ID, o `DTCNAK` + ID | `RPTK` |
| `DTCC` + ID + configuración (294) | 302 | `DTCACK` + ID | `RPTC` |
| `DTCO` + ID + texto de opciones | variable | `DTCACK` + ID | `RPTO` |
| `DTCPING` + ID | 11 | `DTCPONG` + ID | `RPTPING` / `MSTPONG` |
| `DTCCL` + ID | 9 | nada | `RPTCL` |
| `DTCG` + ID + `"%+08.4f%+09.4f"` | 25 | nada | `RPTG` |

Iniciados por el servidor:

| El servidor envía | Cuándo |
|-------------------|--------|
| `DTCCL` + ID | El emplazamiento agotó su tiempo, o el sistema se quitó o se reinició. El emplazamiento vuelve a conectarse. |
| `DTCNAK` + ID | Login rechazado (contraseña, `REG_ACL`, ID desconocido, `MAX_PEERS`). El emplazamiento reinicia su login. |

Qué hace el servidor en un MASTER de trunking:

- El `DTCACK` final (tras `DTCC`) es importante. Con él, DMRGateway inyecta «MasterLogin» hacia dmrtc, y dmrtc envía sus suscripciones.
- `DTCL` marca el peer como emplazamiento. Un `DTCK`, `DTCC` o `DTCPING` de un peer sin esa marca recibe `DTCNAK`.
- Los `RPTL`, `RPTK`, `RPTC`, `RPTO` y `RPTPING` clásicos reciben `MSTNAK`.
- Un `DTCCL` de un emplazamiento no tiene respuesta. DMRGateway puede reabrir su socket en el mismo puerto, y un `DTCNAK` tardío reiniciaría ese login nuevo.
- `DTCO` se guarda en el peer como referencia (`TRUNK_OPTIONS`) y nunca se aplica como OPTIONS de hotspot.
- `DTCG` actualiza la latitud y la longitud del emplazamiento. `DMRG` y `RPTG` se ignoran.
- `DTCSBKN` (petición de baliza) no se envía.
- En un sistema sin `TRUNKING`, cualquier opcode `DTC*` se registra como `Unrecognized command`, como antes.

## DMRD (71 bytes)

| Bytes | Campo |
|-------|-------|
| 0–3 | `DMRD` |
| 4 | Secuencia |
| 5–7 | ID de origen |
| 8–10 | Destino (TG o unidad) |
| 11–14 | ID del emplazamiento |
| 15 | Bits: slot (0x80), llamada unitaria (0x40), tipo de trama (0x30), tipo de dato / secuencia de voz (0x0F) |
| 16–19 | ID de stream |
| 20–52 | Carga DMR (33 bytes) |
| 53–54 | BER, RSSI |
| 55–70 | **UUID de llamada** (16 bytes) |

El UUID es la identidad de la llamada en el lado del trunking: dmrtc deriva de él su ID de stream. Todas las tramas de una llamada llevan el mismo UUID, y cada llamada tiene el suyo. dmrtc ignora el bit de slot y lleva muchas llamadas a la vez por un enlace.

**Desde un emplazamiento:** si la trama mide 71 bytes o más, su UUID se recuerda para su ID de stream. El enrutado recibe después los 55 bytes clásicos. Cada stream de subida tiene su propio estado RX, no el del slot, así que las llamadas simultáneas con el mismo bit de slot no se molestan entre sí.

**Hacia un emplazamiento:** siempre 71 bytes. El servidor escribe el ID del emplazamiento en 11–14, completa BER / RSSI hasta 55 bytes y añade el UUID del stream:

- el UUID que el emplazamiento de origen envió para ese stream (se guarda 60 s después de su última trama);
- si no, un UUID estable: BLAKE2b-128 de `b"ADN-TIER3"` + ID de stream, con los bits de versión 4 y de variante de RFC 4122.

Qué emplazamientos reciben una trama:

| Trama | Emplazamientos |
|-------|----------------|
| Grupo, TG 9990–9999 | Solo el último emplazamiento que activó ese TG en este sistema (hasta 60 s después de la última trama emitida o reproducida) |
| Otro grupo | Todos los emplazamientos conectados del sistema suscritos al TG |
| Unitaria (voz privada, datos) | El emplazamiento donde el destino está registrado en este sistema, salvo que `SUB_MAP` lo haya oído después en otro sitio; si no, su emplazamiento en `SUB_MAP` en este sistema; si no, ninguno |

No hay difusión, y no se aplica ninguna puerta de hotspot (OPTIONS, contención de slot, `SINGLE`). Un emplazamiento nunca recibe de vuelta sus propias tramas.

## DMRT

`DMRT` (4 bytes), el opcode en el byte 4 y después sus campos. Las direcciones son de 24 bits big-endian. Las marcas de tiempo son segundos Unix con signo de 64 bits little-endian. Un mensaje mide como mucho 255 bytes.

El bit `0x40` del opcode marca un mensaje **desde la red**. Un emplazamiento envía `0x01`–`0x0A`, y el servidor responde o reenvía con `0x40` activado. Los opcodes `0x80` y superiores (`0xC1` MasterLogin, `0xC2` MasterClose, `0x81`–`0x87` control de canal) se quedan entre DMRGateway y dmrtc.

| Op | Nombre | Longitud (bytes) | Formato tras el byte 4 | Acción del servidor |
|----|--------|------------------|------------------------|---------------------|
| `0x01` | Registro | 14 + 3n (n = 1..8) | [5–12] marca de tiempo, [13] n, [14…] IDs de radio | Cada radio se acepta salvo que `SUB_ACL` la deniegue (el ID 0 se rechaza). Pasa a estar en este emplazamiento. Si estaba en otro emplazamiento conectado, ese recibe un `0x42` para ella. Respuesta: un `0x41`. |
| `0x41` | Confirmación de registro | 15 + 3n | [5–12] marca de tiempo, [13] bits de estado (bit i = ID i, 1 = aceptado), [14] n, [15…] IDs | Lo envía el servidor. |
| `0x02` | Baja | 14 + 3n | como `0x01` | Se olvida si la radio está en este emplazamiento, junto con su entrada de `SUB_MAP` allí. Siempre se responde con `0x42` repitiendo los IDs. |
| `0x42` | Baja desde la red | 14 + 3n | como `0x01` | Se envía como respuesta a `0x02`, y al emplazamiento anterior cuando una radio hace roaming. |
| `0x03` | Suscripción de grupo | 6 + 3n (n = 1..64) | [5] n, [6…] TG | Cada TG se acepta salvo que esté reservado, lo denieguen las ACL de TG o el emplazamiento pase de 512 TG. Respuesta: `0x43` con cada TG, `0` en cada uno rechazado. |
| `0x04` | Baja de grupo | 6 + 3n | como `0x03` | Cada TG se retira. Respuesta: `0x44` repitiendo todos los TG. |
| `0x05` | Transferencia UDT | 29 + tamaño (tamaño ≤ 46) | [5–20] UUID, [21] tipo (0x80 grupo, 0x40 pide acuse, nibble bajo formato), [22] tamaño, [23–25] origen, [26–28] destino, [29…] carga | Se reenvía como `0x45`. Grupo: a todos los demás emplazamientos suscritos al TG, en cualquier sistema de trunking. Privado: al emplazamiento de la radio destino. El UUID se guarda 60 s para encaminar la aceptación. |
| `0x06` | Aceptación de UDT | 27 | [5–20] UUID, [21–23] origen, [24–26] destino | Se reenvía como `0x46` al emplazamiento que envió el UDT. |
| `0x07` | Establecimiento de llamada privada | 28 | [5–20] UUID, [21] opciones de servicio, [22–24] origen, [25–27] destino | Se reenvía como `0x47` al emplazamiento de la radio llamada. Si no está registrada en otro emplazamiento conectado: `0x49` de vuelta a quien llama. |
| `0x08` / `0x09` | Aceptación / rechazo de llamada privada | 27 | como `0x06` | Se reenvía como `0x48` / `0x49` al emplazamiento de quien llama. |
| `0x0A` | Transferencia de estado | 28 | [5–20] UUID, [21] 0x80 grupo + estado de 7 bits, [22–24] origen, [25–27] destino | Se reenvía como `0x4A`, encaminado como un UDT. No se sigue ninguna respuesta. |

Reglas:

- La longitud debe ser exactamente la que construye dmrtc; cualquier otra se descarta.
- Un mensaje de un emplazamiento con el bit `0x40` o `0x80` activado, un opcode desconocido o un mensaje mal formado se descarta y se registra en `DEBUG` una vez por emplazamiento y opcode.
- Una aceptación o un rechazo solo se reenvía para un UUID que el servidor encaminó en los últimos 60 s, y solo si coincide con el tipo de ese UUID (UDT o llamada privada).
- Los mensajes reenviados son los bytes del emplazamiento con el bit `0x40` añadido. Solo van a emplazamientos conectados y nunca de vuelta al remitente.
- `DMRT` no lleva ID de emplazamiento. El servidor lo deduce de la dirección de origen del datagrama, y solo lo acepta de un emplazamiento conectado (5–255 bytes).

**El dmrtc actual** envía `0x01`–`0x06` y trata `0x41`–`0x46`. Su controlador compara los opcodes entrantes sin el bit `0x40`, así que ignora `0x45` y `0x46`: los SMS entre emplazamientos no llegan a RF hasta que se corrija dmrtc. Todavía no usa `0x07`–`0x0A`.

## Estado

Un registro por proceso, fuera de la configuración. `SIGHUP` lo conserva; un reinicio lo borra.

| Estado | Duración |
|--------|----------|
| Ubicación de una radio (un emplazamiento por radio, en todos los sistemas de trunking) | Hasta la baja, el roaming o la eliminación del sistema. Se mantiene aunque el emplazamiento vuelva a conectarse. |
| Talkgroups por emplazamiento | Hasta la baja de grupo o la salida del emplazamiento |
| UUID de stream | 60 s tras la última trama del stream |
| UUID de UDT / llamada privada | 60 s |
| Origen del eco por sistema y TG | 60 s tras la última trama |
| Estado RX por stream de un emplazamiento | 60 s tras la última trama |

Cuando un emplazamiento se va (tiempo agotado, `DTCCL`, NAK, login nuevo), se borran sus radios, sus talkgroups y sus streams. Se liberan los talkgroups que ningún otro emplazamiento del sistema mantiene.

En el enrutado, el primer emplazamiento de un sistema que se suscribe a un TG da al sistema piernas **TRUNK** en los dos slots. Están activas, nunca caducan y el monitor las muestra como estáticas. Cuando el último emplazamiento retira el TG, vuelven a ser piernas in-band en reposo.

## Implementación

| Capa | Módulo |
|------|--------|
| Dominio (formato en el cable) | `domain/trunking.py` |
| Señalización y registro | `application/trunking/` (`use_cases.py`, `registry.py`, `config.py`) |
| Piernas de enrutado y enrutado unitario | `application/routing/trunk_routing.py`, `application/subscription/trunk_legs_ops.py` |
| Lado MASTER de HBP | `infrastructure/twisted_adapters/hbp_trunking.py` (mezclado en `HBPProtocol`) |
| Cableado | `infrastructure/bootstrap/trunking.py` |

Tests: `tests/trunking/`. `tests/support/dmrtc_wire.py` porta los constructores y analizadores de mensajes de dmrtc, así que los tests usan el formato de bytes del propio cliente.

## Referencias

- Especificación del protocolo v0.6: `docs/Homebrew_DMR_trunking_protocol_v0.6.pdf` en el [repositorio de dmrtc](https://github.com/qradiolink/dmrtc).
- Cliente: `DMRNetwork.cpp` de DMRGateway (`TrunkingEnabled=1`); `networksignalling.cpp` y `controller.cpp` de dmrtc.
- [HBP (DMRD)](hbp.md) para la trama clásica.
