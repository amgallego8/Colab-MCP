# Colab-MCP

Servidor MCP que envuelve el **Colab CLI oficial de Google** (`google-colab-cli`) para que un agente (Claude) pueda crear un runtime con GPU, subir archivos, ejecutar código, descargar resultados y detener la máquina.

No reimplementa nada de Colab: cada herramienta ejecuta un comando de `colab`. No usa el MCP oficial de Colab (`googlecolab/colab-mcp`), que depende de una pestaña abierta del navegador y de que el cliente reciba herramientas dinámicas.

## Estado

- Probado de extremo a extremo con un `colab` **simulado** (39 pruebas, `tests/`). El flujo MCP real (cliente por stdio) está cubierto.
- **No probado todavía contra Colab real**: falta el primer inicio de sesión y una corrida real (ver «Primera corrida»).
- Windows no está soportado por el CLI de Google (solo Linux y macOS).

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `colab_sessions`, `colab_status`, `colab_usage` | Sesiones activas, estado de una sesión, saldo de unidades de cómputo |
| `colab_new` | Crea un runtime (CPU, `gpu` T4/L4/G4/H100/A100, `tpu` v5e1/v6e1, `high_mem`) |
| `colab_install` | Instala paquetes en el runtime |
| `colab_exec` | Ejecuta Python (`code` o `file` local) con `secrets` opcionales |
| `colab_run` | Trabajo de una sola vez: máquina nueva, script, y la libera |
| `colab_upload`, `colab_download`, `colab_ls`, `colab_rm` | Archivos del runtime |
| `colab_log` | Historial de la sesión |
| `colab_stop` | Detiene la sesión y libera la máquina |

Deliberadamente **no** incluye iniciar sesión, `colab auth` ni `drivemount`: son flujos interactivos.

## Seguridad

- **Rutas locales confinadas.** Subir, descargar y ejecutar archivos solo funciona dentro de `COLAB_MCP_WORKDIR`; `../`, rutas absolutas y enlaces simbólicos que salgan se rechazan.
- **Secretos fuera del chat.** `COLAB_MCP_ENV_FILE` apunta a un archivo local `NOMBRE=valor`. El modelo solo conoce los nombres, pasa `secrets=["OPENROUTER_API_KEY"]`, y el valor se envía al kernel con `--env` y se **borra de toda salida**. Límite conocido: mientras corre el comando, el valor es visible en la lista de procesos de tu equipo (`ps`), porque el CLI lo recibe como argumento.
- **Sin shell.** Los comandos se ejecutan con lista de argumentos; nombres de sesión, GPU y paquetes se validan.
- **Recortes.** La salida se limita a `COLAB_MCP_MAX_OUTPUT` caracteres (20 000 por defecto).
- Lo que ejecutes con `colab_exec` corre en máquinas de Google bajo tu cuenta y **consume unidades de cómputo**. Usa `colab_stop` al terminar.

## Instalación (macOS o Linux)

```bash
# 1. uv y el CLI oficial
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install google-colab-cli

# 2. Iniciar sesión UNA vez (flujo de copiar y pegar el código)
colab sessions

# 3. Este servidor
git clone https://github.com/amgallego8/Colab-MCP ~/Colab-MCP
```

Archivo de secretos (fuera de cualquier repositorio, solo tú puedes leerlo):

```bash
mkdir -p ~/.config/colab-mcp
printf 'OPENROUTER_API_KEY=pega-aquí-tu-clave\n' > ~/.config/colab-mcp/secretos.env
chmod 600 ~/.config/colab-mcp/secretos.env
```

Registrar en **Claude Code**:

```bash
claude mcp add colab-cli \
  -e COLAB_MCP_WORKDIR="$HOME/colab-work" \
  -e COLAB_MCP_ENV_FILE="$HOME/.config/colab-mcp/secretos.env" \
  -- uv run --directory ~/Colab-MCP colab-cli-mcp
```

Registrar en **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "colab-cli": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/TU_USUARIO/Colab-MCP", "colab-cli-mcp"],
      "env": {
        "COLAB_MCP_WORKDIR": "/Users/TU_USUARIO/colab-work",
        "COLAB_MCP_ENV_FILE": "/Users/TU_USUARIO/.config/colab-mcp/secretos.env"
      }
    }
  }
}
```

### Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `COLAB_MCP_WORKDIR` | carpeta actual | Única carpeta local a la que el servidor accede |
| `COLAB_MCP_ENV_FILE` | — | Archivo `NOMBRE=valor` con secretos |
| `COLAB_MCP_AUTH` | el del CLI | `oauth2` o `adc` (`--auth` del CLI) |
| `COLAB_MCP_MAX_OUTPUT` | `20000` | Máximo de caracteres devueltos |
| `COLAB_BIN` | `colab` | Ruta del ejecutable del CLI |

## Primera corrida (laboratorio de modelos)

1. `colab_new` con `gpu="T4"` (solo hace falta GPU para Gemma; los modelos en la nube no la usan).
2. `colab_upload` del cuaderno exportado a `.py` y de los recibos.
3. `colab_exec` con `secrets=["OPENROUTER_API_KEY"]`.
4. `colab_download` de los CSV de resultados y `colab_stop`.

> El cuaderno del laboratorio lee la clave con `userdata.get` de Colab y, si no está, de la variable de entorno `OPENROUTER_API_KEY`. Con este servidor se usa la variable de entorno. Que `userdata` funcione desde el CLI **no está documentado**.

## Pruebas

```bash
pip install "mcp<2"
python tests/test_server.py
```

## Limitaciones conocidas

- El CLI documenta como predeterminada la autenticación `adc` en su README, pero la versión instalada al momento de escribir esto muestra `oauth2`. Este servidor no fuerza ninguna salvo que definas `COLAB_MCP_AUTH`.
- La salida de comandos largos (`colab_exec`) llega al terminar, no en vivo.
- No se ha verificado si el CLI mantiene `Colab Secrets` accesibles en el kernel remoto.

## Licencia

Sin definir (decisión del autor).
