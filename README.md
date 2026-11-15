# Android WVD Lab v1.0.0

Aplicación de escritorio para Windows x64 que automatiza la extracción de un CDM **Widevine L3** desde un emulador Android y genera un archivo **`.wvd`** compatible con `pywidevine`.

---

## Descargo de responsabilidad

> **Lee esto antes de usar la aplicación.**

**Android WVD Lab** es una herramienta **exclusivamente educativa, de investigación en seguridad e interoperabilidad**. Su propósito es permitir a investigadores, desarrolladores y estudiantes comprender cómo funciona el sistema de DRM Widevine L3 en un entorno controlado (un emulador Android propiedad del usuario).

Al descargar, ejecutar o utilizar esta aplicación, aceptas que:

1. **Eres el único responsable** del uso que hagas de la herramienta, de los archivos `.wvd`, `client_id.bin`, `private_key.pem` y de cualquier contenido que descifres con ellos.
2. **No está permitido** utilizar esta herramienta para:
   - Piratear, redistribuir o compartir contenido protegido por derechos de autor (películas, series, música, cursos, etc.).
   - Eludir medidas tecnológicas de protección con fines comerciales o de lucro.
   - Acceder a servicios de streaming incumpliendo sus términos de servicio.
   - Vulnerar la legislación aplicable en tu país (por ejemplo, la DMCA en EE. UU., la Directiva 2009/24/CE y la Directiva 2014/26/UE en la UE, la LPI en España, o normativas equivalentes en otras jurisdicciones).
3. El autor **no se hace responsable** de ningún daño directo o indirecto, pérdida de datos, suspensión de cuentas, reclamaciones de terceros o consecuencias legales derivadas del uso de esta aplicación.
4. **Sobre `assets/kaltura.apk`**: la aplicación **incluye** el APK de *Kaltura Device Info* como utilidad de conveniencia para lanzar el aprovisionamiento de Widevine dentro del emulador. Dicho APK es propiedad de **Kaltura, Inc.** y se distribuye únicamente con el propósito previsto por su autor (consulta de información del dispositivo y *provisioning* de DRM). No se reclama ningún derecho sobre él. Si eres titular de derechos y deseas que se retire del repositorio o de las releases, abre un *issue* y se eliminará a la mayor brevedad. El uso del APK por tu parte queda sujeto a su propia licencia y a los términos de Kaltura.
5. La herramienta **no incluye** ningún CDM, clave ni certificado de Widevine. Cualquier `.wvd` generado se produce **localmente** en tu máquina a partir del emulador que tú controlas.
6. El software se distribuye **"tal cual"** (*as is*), sin garantía de ningún tipo, expresa o implícita, incluidas las garantías de comerciabilidad, idoneidad para un fin concreto y no infracción.
7. El proyecto **no está afiliado, patrocinado ni respaldado** por Google LLC, Widevine, Kaltura, Netflix, YouTube ni ninguna otra entidad cuyos productos o servicios puedan verse afectados.
8. El autor puede **modificar, suspender o retirar** el proyecto en cualquier momento y sin previo aviso.

Si no estás de acuerdo con alguno de estos puntos, **no descargues ni utilices** la aplicación.

### Alternativas legítimas

Si tu objetivo es reproducir contenido propio o con licencia en un entorno controlado, considera usar:

- Contenido sin DRM o con licencias abiertas (Creative Commons, dominio público).
- Servicios oficiales que ofrezcan descargas offline dentro de su propia app.
- Herramientas de captura y transcodificación sobre contenido del que poseas los derechos.

---

## ¿Qué hace?

1. Descarga e instala el SDK de Android (`platform-tools`, `emulator`, una JRE portátil si hace falta) y una imagen **Google APIs x86_64** (preferentemente API 34).
2. Crea un AVD temporal con esa imagen.
3. Arranca el emulador con `-writable-system` y renderizado por software (`swiftshader`).
4. Ejecuta `adb root`, descarga `frida-server` (misma versión que el cliente Frida) y lo inicia como root.
5. Instala la app **Kaltura Device Info** desde `assets/kaltura.apk`.
6. Lanza **KeyDive** en modo pasivo (`keydive -s <serial> -w -k -o outputs`).
7. **Tú, manualmente**, aprovisionas Widevine desde la app del emulador (ver siguiente sección).
8. KeyDive captura las claves y genera el `.wvd` en `outputs/`.
9. Al terminar, el emulador se apaga y el AVD se elimina (por seguridad).

## Flujo de aprovisionamiento (muy importante)

Cuando el emulador esté abierto y KeyDive esté escuchando, **debes aprovisionar Widevine a mano**. La app Kaltura se lanza automáticamente; si no aparece, ábrela desde el cajón de apps (icono **Device Info**).

Pasos exactos en el emulador:

1. Pulsa **Device Info** (o **Device Intro** según la versión del emulador) para abrir la app.
2. Aparecerá un diálogo de compatibilidad ("*This app was built for an older version of Android…*"): pulsa **OK**.
3. Pulsa el **botón flotante rojo** de la esquina inferior derecha.
4. Elige **Provision Widevine**.
5. Confirma con **YES** en el diálogo "*Are you sure you want to attempt Widevine Provisioning?*".
6. Espera a que KeyDive capture las claves. El `.wvd` aparecerá en `outputs/`.

> Si tienes otra app que haga provisioning (Netflix, YouTube, etc.) puedes usarla en lugar de Kaltura; KeyDive escucha a cualquier proceso.

## Uso rápido

1. Descarga y extrae `Android-WVD-Lab-v1.0.0-windows-x64.zip` en una carpeta con varios GB libres.
2. Ejecuta `Android-WVD-Lab.exe`.
3. Pulsa **Preparar**. La primera vez abrirá una consola para aceptar las licencias de Android.
4. Pulsa **Obtener WVD**.
5. Sigue el **flujo de aprovisionamiento** de la sección anterior.
6. El `.wvd` queda en `outputs/`.

- **Borrar** elimina los AVD de la aplicación (previa confirmación).
- **Apagar** cierra los emuladores activos y elimina sus AVD y datos.

## Salida

El archivo `.wvd` se guarda en `outputs/`. KeyDive también puede dejar `client_id.bin` y `private_key.pem` en la misma carpeta.

## Requisitos

- Windows 10/11 x64, conexión a Internet y virtualización de hardware habilitada (HAXM / WHPX / Hyper-V).
- Las descargas de Android pueden ocupar varios GB:
  - `sdk/` → SDK e imágenes
  - `runtime/` → JRE portátil y Python embed (portable)
  - `.android/` → AVD
  - `outputs/` → `.wvd`, `.bin`, `.pem`
- `adb root` funciona en imágenes **Google APIs** (no en las de Play Store).
- `assets/kaltura.apk` se incluye en la distribución para facilitar el aprovisionamiento automático. Es propiedad de Kaltura, Inc. y se usa únicamente con su propósito previsto; consulta el [Descargo de responsabilidad](#descargo-de-responsabilidad). Si por cualquier motivo el APK no estuviera disponible, puedes lanzar manualmente cualquier otra app que haga *provisioning* de Widevine; KeyDive escucha a cualquier proceso.

> El uso de esta herramienta está sujeto al [Descargo de responsabilidad](#descargo-de-responsabilidad).