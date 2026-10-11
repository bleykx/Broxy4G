import json
import os
import tempfile

from .nftables import NftablesChain, NftablesRuleset, NftablesTable
from .shell import ShellAdapter


class RealNftables:
    """Applique des tables nftables dédiées, sans jamais toucher au reste.

    L'application est idempotente et *scopée* : chaque table gérée est
    (re)déclarée puis `flush`ée avant redéfinition complète, ce qui évite tout
    `flush ruleset` global (qui effacerait les règles Docker de l'hôte).
    """

    def __init__(self, shell: ShellAdapter, work_dir: str | os.PathLike[str] | None = None):
        self.shell = shell
        self._managed: set[tuple[str, str]] = set()
        # Répertoire d'écriture du fichier nft temporaire. Explicite pour ne
        # jamais dépendre du temp dir système, souvent en lecture seule sous
        # durcissement systemd (`ProtectSystem=strict`).
        self._work_dir = str(work_dir) if work_dir is not None else None
        if self._work_dir is not None:
            os.makedirs(self._work_dir, exist_ok=True)

    async def _run_checked(self, action: str, command: list[str]) -> None:
        result = await self.shell.run(command)
        if result.returncode != 0:
            raise RuntimeError(
                f"{action} failed (rc={result.returncode}): {result.stderr or result.stdout}"
            )

    async def apply_ruleset(self, ruleset: "NftablesRuleset") -> None:
        nft_syntax = self._ruleset_to_nft_syntax(ruleset)
        for table in ruleset.tables.values():
            self._managed.add((table.family, table.name))
        if not nft_syntax.strip():
            return

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".nft", delete=False, dir=self._work_dir
        ) as f:
            f.write(nft_syntax)
            temp_file = f.name

        try:
            await self._run_checked("nft apply", ["nft", "-f", temp_file])
        finally:
            os.unlink(temp_file)

    async def get_ruleset(self) -> "NftablesRuleset":
        result = await self.shell.run(["nft", "-j", "list", "ruleset"])
        if result.returncode != 0:
            return NftablesRuleset(tables={})
        return self._parse_json_ruleset(result.stdout)

    async def flush(self) -> None:
        """Vide uniquement les tables gérées (jamais le ruleset global)."""
        for family, name in sorted(self._managed):
            await self.shell.run(["nft", "flush", "table", family, name])
        self._managed.clear()

    def _ruleset_to_nft_syntax(self, ruleset: "NftablesRuleset") -> str:
        lines: list[str] = []
        for table in ruleset.tables.values():
            header = f"table {table.family} {table.name}"
            lines.append(f"{header} {{}}")
            lines.append(f"flush {header}")
            lines.append(f"{header} {{")
            for chain in table.chains.values():
                lines.append(f"  chain {chain.name} {{")
                if chain.hook:
                    priority = chain.priority
                    policy = chain.policy or "accept"
                    lines.append(
                        f"    type {chain.type} hook {chain.hook} "
                        f"priority {priority}; policy {policy};"
                    )
                for rule in sorted(chain.rules, key=lambda r: r.priority):
                    lines.append(f"    {rule.match} {rule.action}".rstrip())
                lines.append("  }")
            lines.append("}")
            lines.append("")
        return "\n".join(lines) + ("\n" if lines else "")

    def _parse_json_ruleset(self, output: str) -> "NftablesRuleset":
        try:
            data = json.loads(output)
        except (ValueError, TypeError):
            return NftablesRuleset(tables={})

        items = data.get("nftables", []) if isinstance(data, dict) else []
        tables: dict[str, NftablesTable] = {}
        for item in items:
            table = item.get("table")
            if table:
                name = table.get("name", "")
                tables[name] = NftablesTable(
                    name=name, family=table.get("family", "ip"), chains={}
                )
        for item in items:
            chain = item.get("chain")
            if not chain:
                continue
            parent = tables.get(chain.get("table", ""))
            if parent is None:
                continue
            name = chain.get("name", "")
            parent.chains[name] = NftablesChain(
                name=name,
                rules=[],
                type=chain.get("type"),
                hook=chain.get("hook"),
                priority=chain.get("prio", 0),
                policy=chain.get("policy"),
            )
        return NftablesRuleset(tables=tables)
