# metocean-ai-scoreboard — Claude Code

## Délégation sous-agents

Pour tout chantier multi-fichiers, déléguer à des sous-agents **en parallèle**
dès que les périmètres de fichiers sont disjoints (couche données vs pages UI,
docs vs code, repo scoreboard vs site ODC). Séquencer uniquement les vraies
dépendances. Terminer chaque chantier par une passe d'auto-vérification
(tests + build + relecture du diff) confiée à un agent dédié, **avant** commit.

## Contexte récent

Lire d'abord [`docs/review_codex_2026-08-05.md`](docs/review_codex_2026-08-05.md).
Cette note résume les invariants de fiabilité et le nouveau protocole scientifique
introduits par les commits `dd5924b` et `1a9b8fa`.

## Avant chaque commit de code

Lancer `/simplify` sur les changements, puis appliquer les corrections, **avant**
de commiter.

Ne s'applique pas aux commits sans code : `chore(data)` du cron quotidien,
backfills, `docs:`.
