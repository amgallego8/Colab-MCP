<!-- Draft written with AI assistance. It describes what the code does today. It is not legal advice:
     the maintainer should review it before relying on it. -->

# Terms of use and privacy

> 🇪🇸 Resumen en español al final.

## What this project is

Colab-MCP is an independent, community-made tool that lets an AI agent drive Google Colab and, optionally, create and delete Colab notebooks in your own Google Drive. It is published by [`amgallego8`](https://github.com/amgallego8) on GitHub. It is not made, endorsed or supported by Google.

## What it does with your data

- **No server, no collection.** The maintainer runs no server for this project and receives nothing: no tokens, no notebooks, no usage statistics. There is no telemetry in the code.
- **Where data goes.** Your agent's requests go from your computer straight to Google (Colab or Drive) using *your* Google sign-in. Notebook contents you upload go to *your* Drive.
- **What is stored locally.** The sign-in token (`token.json`, owner-only permissions), an audit log (`audit.log`: time, action, file id and name; never file contents), and, if you choose, your own secrets file. All of it stays on your computer.
- **Drive permission.** Only `drive.file`: the tool can reach files that this app created, not the rest of your Drive.
- **Undo.** `colab-cli-mcp-drive logout` deletes the token and revokes it at Google. You can also remove the app at <https://myaccount.google.com/permissions>.

## Two ways to sign in

- **Your own OAuth client (default).** You create the Google Cloud project; you are the app owner and you answer to Google for it.
- **The shared client (only if a build ships `shared_client.json`).** The project named on Google's consent screen belongs to the maintainer, who is then the app owner toward Google. Using it does not give the maintainer access to your account or files: the token is issued to your computer. You are asked to choose explicitly; it is never used silently.

## Your responsibilities

- Keep your token and client files private and your computer secure. Whoever can read your token can use it until you log out.
- Follow Google's terms for the services you use: [Google Terms of Service](https://policies.google.com/terms), the Google Cloud and Drive API terms, and Colab's own terms and usage limits. Code you run in Colab executes on Google's machines under your account and uses your compute units.
- Do not use this tool to violate the law, to harm others, to evade Google's limits or controls, or to store or process data you have no right to handle.
- Review any notebook or code before you run it. Names and contents that come back from Drive are untrusted data.
- Keep the approval prompt on for destructive tools (`drive_notebook_delete`) in your MCP client.

## What is deliberately not possible

Permanent deletion, sharing, changing permissions, reading other Drive files, and signing in through a tool (sign-in is a terminal command you run yourself). See the threat model in the README for what remains possible and the residual risks.

## No warranty, limited liability

The software is provided "as is", without warranty of any kind. It has not been validated against live Google services yet (see the README, *Known unknowns*). The maintainer is not liable for lost or deleted notebooks, compute costs, account actions taken by Google, or any other damage arising from its use, to the extent the law allows. The code is licensed under the Apache License 2.0 (see `LICENSE`), which also contains its own warranty disclaimer and limitation of liability.

## Changes and contact

These terms may change; the version in the repository is the current one. Questions or security reports: open an issue at <https://github.com/amgallego8/Colab-MCP/issues> (never include tokens, client files or notebook contents).

---

## Resumen en español

- **Es una herramienta independiente de la comunidad**, no de Google.
- **No hay servidor ni recolección de datos:** el mantenedor no recibe tokens, cuadernos ni estadísticas. Todo va de tu computador a Google con tu propia sesión. En tu equipo quedan el token (solo tú puedes leerlo) y un registro de auditoría sin contenidos.
- **Permiso en Drive:** solo `drive.file` (archivos creados por esta app).
- **Cliente propio (por defecto):** tú eres el dueño del proyecto y respondes ante Google. **Cliente compartido (opcional):** el dueño ante Google es el mantenedor, pero no obtiene acceso a tu cuenta; siempre se pide elegirlo explícitamente.
- **Tus responsabilidades:** proteger tu token y tu equipo, cumplir los términos de Google y de Colab (el código corre en máquinas de Google con tu cuenta y gasta tus unidades), no usarla para fines ilícitos o dañinos, revisar lo que ejecutas y mantener la aprobación por llamada en el borrado.
- **Sin garantía:** se ofrece «tal cual». El mantenedor no responde por cuadernos perdidos, costos de cómputo ni acciones de Google, en la medida que la ley lo permita.
