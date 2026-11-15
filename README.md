# Android WVD Lab v1.0.0

Aplicación de escritorio para Windows x64 que automatiza la extracción de un CDM **Widevine L3** desde un emulador Android y genera un archivo **`.wvd`** compatible con `pywidevine`.

## ¿Qué hace?

1. Descarga e instala el SDK de Android (`platform-tools`, `emulator`, una JRE portátil si hace falta) y una imagen **Google APIs x86_64** (preferentemente API 34).
2. Crea un AVD temporal con esa imagen.
3. Arranca el emulador con `-writable-system` y renderizado por software (`swiftshader`).
4. Ejecuta `adb root`, descarga `frida-server` (misma versión que el cliente Frida) y lo inicia como root.
5. Instala la app **Kaltura Device Info** desde `assets/kaltura.apk` (si está presente).
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
- `assets/kaltura.apk` es necesario para el aprovisionamiento automático. Sin él, deberás lanzar manualmente cualquier app que haga provisioning de Widevine. Si clonas el repo, el APK **no** está incluido (motivos de copyright): colócalo tú en `assets/`.
