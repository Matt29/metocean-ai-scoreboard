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

## La doc ne doit jamais être en retard sur le code

Un commit qui change un comportement met à jour, **dans le même commit**, la doc
qu'il périme. Pas « plus tard », pas dans un commit `docs:` de rattrapage : entre
les deux, la doc ment, et quelqu'un — humain ou agent — raisonnera dessus.

À passer en revue à chaque fois : `docs/plan-dev-modele.md`,
`docs/demandes-produit.md`, `docs/data-sources.md`, `docs/model-eval.md`,
`docs/dev-dashboard.html`, `README.md`. Ne jamais réécrire à la main un fichier
**généré** par le pipeline.

Deux règles de fond :

- **Toute mesure citée porte sa date et le commit sur lequel elle a été faite.**
  Un chiffre sans ces deux informations est invérifiable et deviendra faux en
  silence.
- **Distinguer `mesuré` / `raisonné` / `inconnu`.** Un mécanisme correct dont
  l'amplitude n'a pas été mesurée s'écrit « non mesuré », jamais avec un ordre de
  grandeur plausible. Voir `docs/biais-forcage-jours-reconstitues.html` pour le
  format.

Motif : le 2026-08-05, `docs/demandes-produit.md` décrivait encore le pipeline
d'avant les correctifs du 2026-08-04 (jambe ERA5, MFWAM). Deux explications
successives en ont été tirées, toutes deux fausses.

## Vocabulaire

Les données arrivent par des **voies** (voie marée, voie houle, voie vent) — pas
des « jambes », calque de l'anglais `leg` employé dans les commentaires du code,
et pas des « branches », déjà pris par git. Le forçage est **stratifié par âge de
run**, terme natif du projet.

## Avant chaque commit de code

Lancer `/simplify` sur les changements, puis appliquer les corrections, **avant**
de commiter.

Ne s'applique pas aux commits sans code : `chore(data)` du cron quotidien,
backfills, `docs:`.
