# PRUEBA EN MAC (M1 / M2) - 10 minutos

No hace falta instalar nada raro: se usa el instalador .dmg que genera la
compilacion automatica de GitHub (runners Apple Silicon).

---

## 1. Instalar

1. Descarga el .dmg de la ultima compilacion (Actions > artefactos, o el Release).
2. Abrilo y arrastra la app a Aplicaciones.
3. Primera vez: clic derecho > Abrir (macOS pide confirmacion de app no notarizada).

## 2. Abrir y entrar

4. Abre el programa e inicia sesion con tu usuario.

## 3. Ventanas (lo que cambiamos)

5. Abre CADA MODULO del menu lateral. En todos debe:
   - abrirse en SU PROPIA VENTANA, maximizada (sin tapar la barra de menu de macOS);
   - mostrar la barra oscura con Actualizar / Minimizar / Restaurar / Cerrar;
   - dejar el DASHBOARD visible en la ventana principal.
6. Prueba en uno: Minimizar (debe ir al Dock) y luego clic en el icono: vuelve.
7. Prueba Restaurar y Expandir, y la tecla F11.
8. Pulsa Actualizar: debe salir el letrero verde de 1,5 s sin abrir mensajes.
9. Cierra el modulo con el boton Cerrar y con la X roja de la ventana.

## 4. Documentos (si aplica)

10. Flota > Contratos y Actas: elige un chofer y genera un contrato y un acta
    (deben salir en PDF y guardarse en la carpeta de Drive).
11. Compras > Escanear desde el telefono: deben aparecer LOS DOS QR.

## 5. Si algo falla

Manda estos archivos (son la caja negra):

   ~/Library/Application Support/BlackCube/escaner_movil.log      (Eventos)
   ~/Library/Application Support/ControlFlota/escaner_movil.log   (Flota)

y una captura de la ventana con el problema.

---

## Comprobacion automatica (sin abrir la app)

En el Mac, dentro de la carpeta del proyecto:

   python3 prueba_compatibilidad.py

Debe terminar con:  RESULTADO: N comprobaciones OK - 0 fallos

---

## Que queda cubierto sin un Mac

| Cosa                                            | Como se valida                               |
|-------------------------------------------------|----------------------------------------------|
| Que el codigo no use nada solo-Windows          | prueba_compatibilidad.py (simula macOS) + CI |
| Que compile la app en Apple Silicon             | GitHub Actions macos-latest (runners M1)     |
| Que las librerias tengan version arm64          | verificado en PyPI (macosx_11_0_arm64)       |
| Apariencia exacta y gestor de ventanas de macOS | requiere el Mac (esta guia)                  |
