# Control de Clientes y Apartados — Versión en la nube

Aplicación multiusuario para **Muebles Los Ángeles Cartago**, preparada para funcionar con una base de datos central PostgreSQL y ser utilizada desde computadoras, celulares y tabletas.

## Funciones

- Inicio de sesión con usuarios `ADMIN` y `VENDEDOR`.
- Clientes con código único, cédula, teléfono y dirección.
- Apartados con cálculo automático de fecha límite: 15 días por quincena.
- Pago inicial, abonos, saldo automático y estados `ACTIVO`, `VENCIDO` y `PAGADO`.
- Recibos numerados e imprimibles.
- Reportes y exportación CSV.
- Administración de usuarios y cambio de contraseña.
- Auditoría de ingresos, cambios y abonos.
- Respaldo JSON e importación de respaldos de la versión portátil.
- Compatibilidad con PostgreSQL en producción y SQLite para pruebas locales.

## Ejecutar localmente

1. Instale Python 3.12 o superior.
2. Abra una terminal dentro de esta carpeta.
3. Cree y active un entorno virtual.
4. Instale dependencias:

```bash
pip install -r requirements.txt
```

5. En Windows PowerShell configure las variables iniciales:

```powershell
$env:ADMIN_USERNAME="admin"
$env:ADMIN_PASSWORD="CambieEstaClave123!"
$env:SECRET_KEY="una-clave-larga-y-aleatoria"
python app.py
```

6. Abra `http://127.0.0.1:5000`.

En desarrollo, si no se define `ADMIN_PASSWORD`, se crea temporalmente el usuario `admin` con la contraseña `Admin123!`. No use esa contraseña al publicar.

## Publicar en Render

El proyecto incluye dos configuraciones:

- `render.yaml`: configuración recomendada con PostgreSQL persistente de pago (`basic-256mb`, 1 GB).
- `render-prueba-30-dias.yaml`: alternativa de demostración con PostgreSQL gratuito temporal. No debe usarse como base definitiva del negocio.

El flujo recomendado es:

1. Crear un repositorio privado en GitHub y subir esta carpeta.
2. En Render, crear un **Blueprint** y conectar el repositorio.
3. Render detectará el servicio web y la base PostgreSQL definidos en `render.yaml`. Para usar la configuración temporal, indique `render-prueba-30-dias.yaml` como ruta del Blueprint.
4. Cuando Render solicite la variable `ADMIN_PASSWORD`, escriba una contraseña segura de al menos 8 caracteres.
5. Finalizado el despliegue, abra la dirección `https://...onrender.com` asignada.

También puede desplegarlo manualmente con:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Variables: `APP_ENV=production`, `SECRET_KEY`, `DATABASE_URL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_NAME`.

## Migrar los datos de la aplicación portátil

1. En la aplicación portátil, abra **Respaldos** y descargue el archivo JSON.
2. Entre a esta aplicación como administrador.
3. Abra **Respaldos**.
4. Seleccione el JSON, escriba `REEMPLAZAR` y ejecute la importación.

La importación reemplaza los datos comerciales actuales. Antes de importar, descargue un respaldo de la nube.

## Seguridad operativa

- Publique únicamente mediante HTTPS.
- Use una contraseña diferente para cada usuario.
- Desactive inmediatamente usuarios que ya no trabajen con el sistema.
- Descargue respaldos JSON periódicos. Confirme en el proveedor qué respaldos administrados incluye el plan contratado.
- No comparta la variable `SECRET_KEY`, la contraseña de PostgreSQL ni el archivo `.env`.


## Importante sobre el alojamiento

El servicio web está configurado inicialmente en el plan gratuito de Render y puede suspenderse cuando no se utiliza. La configuración recomendada mantiene la base PostgreSQL en un plan persistente de pago. Revise los precios vigentes antes de confirmar el despliegue.
