"""Relais bidirectionnel brut entre deux paires de flux (aucun protocole).

Le relayage se fait en parallèle dans les deux sens, par morceaux limités,
jusqu'à ce que les deux directions soient terminées. Ce module ne connait rien
au HTTP : il ne lit que des octets. Il ne ferme jamais les writers : c'est
l'appelant qui décide de leur sort (demi-ferme à la fin de chaque sens).
"""

import asyncio

CHUNK_SIZE = 64 * 1024


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copie ``reader`` → ``writer`` jusqu'à EOF puis demi-ferme ``writer``.

    Toute erreur de transport (``ConnectionError``/``OSError``) est absorbée :
    un flux mort ne doit pas faire lever une exception au relayeur.
    """
    try:
        while True:
            data = await reader.read(CHUNK_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    try:
        writer.write_eof()
    except (OSError, RuntimeError):
        pass


async def splice(
    a_reader: asyncio.StreamReader,
    a_writer: asyncio.StreamWriter,
    b_reader: asyncio.StreamReader,
    b_writer: asyncio.StreamWriter,
) -> None:
    """Relaye les deux sens en parallèle jusqu'à ce que les deux flux soient finis.

    Deux tâches de pompage courent simultanément ; la fin de l'un ne coupe pas
    l'autre avant que la copie ne soit complète. ``return_exceptions=True``
    garantit qu'une direction ne masque pas la fin de l'autre ; tout le reste
    est annulé proprement dans un ``finally``.
    """
    tasks = [
        asyncio.create_task(_pump(a_reader, b_writer)),
        asyncio.create_task(_pump(b_reader, a_writer)),
    ]
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
