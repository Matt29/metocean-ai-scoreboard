# Revue Codex - 2026-08-05

Cette note est le point d'entrée rapide pour Claude Code après l'audit et les
corrections des 4 et 5 août 2026. Elle couvre les commits suivants, construits
sur `788e717` :

- `dd5924b` - fiabilisation des releases et des runs quotidiens ;
- `1a9b8fa` - validation rolling-origin, intervalles de confiance et scores
  publics pondérés.

## Résumé opérationnel

- Une release d'entraînement évalue toutes les stations avant de modifier les
  artefacts live. Modèles et `gate.json` sont ensuite promus ensemble avec
  rollback en cas d'exception.
- Un gate absent, vide, incomplet ou mal formé arrête `daily` et `backfill` avec
  un code non nul. Un run où toutes les stations publiables échouent devient
  également rouge.
- Les observations de bouées, qui expirent après environ 96 heures, sont
  archivées et commitées avant le scoreboard quotidien. Un échec du calcul ne
  fait donc plus perdre cette fenêtre de collecte.
- Les stations `wave` et `wind` ne passent plus sur un unique test saisonnier.
  Avec assez d'historique, le verdict repose sur quatre origines temporelles et
  un IC95 % par station.
- Les scores publics donnent maintenant le même poids à chaque observation
  valide, via `n_points`, et non le même poids à chaque journée.
- `docs/model-eval.md` reste un rapport généré représentant le dernier
  entraînement réellement exécuté. Le nouveau format avec folds et IC apparaîtra
  lors du prochain retrain ; ne pas fabriquer ces valeurs manuellement.

## 1. Fiabilité des releases et des runs

### Entraînement transactionnel

`pipeline/scripts/train.py` sépare maintenant strictement trois phases :

1. évaluer toutes les stations sans écrire d'artefact live ;
2. valider et fusionner le gate complet ;
3. sérialiser dans un répertoire de staging, puis promouvoir modèles et
   `gate.json` via `model.promote_transaction()`.

Avant promotion, chaque destination existante est copiée. Si un remplacement
échoue, toutes les destinations sont restaurées octet pour octet. Cette garantie
est un rollback **dans le processus** ; elle ne prétend pas rendre plusieurs
fichiers atomiques face à un `SIGKILL` ou à une panne machine.

Un entraînement ciblé avec `--station` exige un gate précédent couvrant toutes
les stations configurées. Un entraînement complet peut reconstruire le gate,
mais le résultat final doit être complet et contenir au moins un `pass: true`.

### Daily, backfill et sources

- `daily.validate_gate()` exige une entrée `pass`/`weak` booléenne pour chaque
  station et au moins une station publiable.
- Une panne isolée reste enregistrée comme `missing` et n'empêche pas les autres
  stations de publier.
- Si toutes les stations passant le gate sont `missing`, `DailyRunError` fait
  sortir la CLI avec un code non nul.
- `backfill` applique la même validation de gate et la CLI transforme les erreurs
  de configuration en échec explicite.
- Candhis lève désormais `SourceError` si la réponse est vide ou si toutes les
  observations sont éliminées par les filtres qualité.

### Workflows GitHub

Dans `.github/workflows/daily.yml`, les observations de bouées sont archivées et
commitées séparément **avant** le scoreboard. Le commit suivant ne prend que les
données issues d'un scoreboard/backfill réussi, les prévisions archivées et les
constantes harmoniques autorisées.

La CI ignore les commits ne modifiant que :

- `pipeline/data_forecast_archive/**` ;
- `pipeline/data_obs_archive/**`.

## 2. Protocole scientifique actuel

### Split rolling-origin pour `wave` et `wind`

Le protocole est défini dans `pipeline/scripts/train.py` :

- fenêtre de test par défaut : 90 jours d'émission ;
- historique minimal pour revendiquer une évaluation multi-saisons : 730 jours ;
- quatre origines espacées d'au moins 90 jours ;
- si `--test-days` dépasse 90, le stride est élargi à cette durée afin que les
  fenêtres ne se chevauchent jamais ;
- purge de 48 heures entre le dernier jour d'émission de train et l'origine du
  test ;
- couverture minimale de chaque fold : 80 % des jours attendus ;
- toutes les lignes d'un même jour d'émission restent dans le même split.

Pour chaque origine, la baseline physique est re-sélectionnée uniquement sur le
passé disponible avant ce fold. La sélection du modèle ML se fait sur une
validation interne au train du fold ; le test ne choisit jamais le candidat.
Les baselines peuvent donc différer entre folds : `fold_baselines` conserve cette
liste dans la ligne d'évaluation, le rapport et `gate.json`. `baseline_model`
désigne séparément la baseline re-sélectionnée pour l'artefact de production.

Après le backtest, le candidat choisi par la validation la plus récente est
ré-entraîné sur tout l'historique disponible. Ces données ne servent pas à
recalculer le verdict déjà verrouillé.

### Historique insuffisant

Avec moins de 730 jours, le code produit au mieux un `holdout dégradé` :

- `evaluation_ready` vaut `false` ;
- le protocole ne prétend pas être multi-saisons ;
- la station `wave`/`wind` ne peut pas passer le gate, même avec un bon gain
  ponctuel.

C'est une contrainte volontaire de qualité scientifique. Les fichiers
`pipeline/data_train/*_raw.parquet` présents couvrent actuellement environ 914
jours pour les stations de vent. Les stations de vagues n'ont pas encore les
archives raw équivalentes : elles restent donc sur leur ancien artefact/gate tant
qu'elles ne sont pas ré-entraînées avec un corpus admissible.

### Intervalle de confiance et gate

L'IC95 % est un bootstrap déterministe de 2 000 réplications. L'unité
d'échantillonnage est le **jour d'émission entier**, jamais la ligne : les leads
1-48 h d'un même run sont corrélés et ne doivent pas créer de pseudo-réplications.
Le biais constant de la baseline est recalculé dans chaque réplication et pour
chaque fold.

Une station passe uniquement si :

```text
evaluation_ready
AND gain_debiased >= 5 %
AND gain_debiased_ci95_low > 0
```

Le gate conserve notamment `n_folds`, `n_issue_days`, `test_days`,
`evaluation_protocol`, `evaluation_ready`, `ci_unit`, les deux bornes de l'IC et
`fold_baselines`.

La réserve scientifique sur les vagues reste valable : tant que les véritables
runs de prévision passés ne sont pas archivés, leur replay Open-Meteo historique
reste un plafond et non une mesure parfaite du skill opérationnel.

## 3. Pondération des scores publics

`pipeline/src/scoreboard/publish.py::compute_scores()` utilise désormais :

```text
MAE_fenêtre = somme(MAE_jour * n_points_jour) / somme(n_points_jour)
```

Le choix produit est donc **une observation valide = une voix**. Une journée
tronquée ne pèse plus autant qu'une journée complète.

Compatibilité :

- un historique ancien sans `n_points` reçoit un poids de repli égal à 1 ;
- une valeur invalide (`0`, négative, booléenne, fractionnaire, texte ou non
  finie) reçoit également le poids 1 pour ne pas interrompre la publication ;
- les fenêtres restent calendaires (`7d`, `30d`, `all`) ;
- les jours `missing` restent exclus ;
- les jours associés à une ancienne baseline restent exclus des scores de la
  baseline courante.

## 4. Invariants à préserver

Pour toute modification future :

1. Ne jamais faire de split aléatoire ni de split sur le temps de validité : le
   groupe causal est le jour d'émission.
2. Ne jamais construire le train d'un fold comme simple complément du test : ce
   complément contient les données futures. Utiliser un masque explicitement
   antérieur à l'origine.
3. Ne jamais choisir baseline ou modèle sur un fold de test.
4. Ne pas bootstrapper les leads individuellement.
5. Ne pas présenter un `holdout dégradé` comme une validation multi-saisons.
6. Conserver la complétude de `gate.json` et la promotion groupée modèles + gate.
7. Ne pas réintégrer les archives de données dans les déclencheurs CI.
8. Ne pas revenir à une moyenne simple des MAE journalières sans décision produit
   explicite et migration du contrat.

## 5. Validation exécutée

- Suite complète : `240 passed`.
- Lint : `ruff check src scripts tests` réussi.
- `git diff --check` réussi.
- Test réel sans publication sur Ouessant : 914 jours d'historique, 4 folds,
  17 238 points de test. Commande :

```bash
cd pipeline
UV_CACHE_DIR=.uv-cache uv run python scripts/train.py \
  --station ouessant --model ridge --ablate wind_u10
```

- Deux revues indépendantes, Standards et Spec, ont été rejouées après les
  corrections finales : aucun finding P0-P3 résiduel.

Les avertissements restants de la suite proviennent principalement de la
désérialisation NumPy/joblib et d'UTide ; ils ne font pas échouer les tests.

## 6. Prochaines étapes utiles

1. Accumuler les vrais runs de vagues et observations correspondantes jusqu'à un
   historique permettant les quatre folds saisonniers.
2. Lors du prochain retrain, inspecter le nouveau `docs/model-eval.md` et les
   champs d'incertitude de `pipeline/models/gate.json` avant publication.
3. Surveiller la durée du retrain complet : chaque station `wave`/`wind` exécute
   désormais plusieurs fits par fold, puis un refit de production.
4. Envisager un block bootstrap de plusieurs jours si l'autocorrélation météo
   inter-journalière doit être modélisée plus conservativement.
