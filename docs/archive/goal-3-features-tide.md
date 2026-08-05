# GOAL — les 3 features restantes du plan de modélisation (chemin `tide`)

Autonome. **Ne me pose aucune question** : chaque arbitrage possible est tranché
par une règle ci-dessous. Si une règle ne couvre pas le cas, applique la plus
conservatrice (= ne pas garder la feature) et écris pourquoi dans le rapport.

---

## Objectif

Implémenter, **mesurer**, et garder ou jeter les trois features encore ouvertes
de `docs/plan-dev-modele.md` § « Features à tester », sur les deux stations
`tide` (brest, saint-malo) :

1. **Interaction marée-surcote** — dérivée temporelle de la prédiction
   harmonique (`dh/dt`, proxy du courant de marée). Prioritaire : c'est la seule
   piste avec un appui mesuré (§ Réserves ouvertes, « Plafond propre à
   saint-malo » : composante semi-diurne à phase non stationnaire,
   autocorrélation 0,17 à 6 h puis 0,84 à 12 h, invisible pour `hour_sin/cos`
   qui sont solaires 24 h).
2. **Tension de vent** — `wind_stress_u = wind_u10 * hypot(u,v)` et
   `wind_stress_v = wind_v10 * hypot(u,v)`. La surcote répond à τ ∝ U², pas à U.
   **Deux colonnes u/v, pas un `U²·cos(θ−θ₀)` avec un axe par station** : un θ₀
   en config serait un paramètre réglé à la main, donc une porte au
   sur-ajustement silencieux.
3. **Tendance de pression** — `pressure_anom` différenciée à 3 h et 6 h.

Fin de course attendue : le code, les mesures, les artefacts ré-entraînés, la
doc à jour, un handoff.

---

## Contexte technique — lis ça avant de coder

- Tout passe par **`pipeline/src/scoreboard/features.py::build_features`**, le
  chemin unique entraînement/service. C'est la garantie centrale du projet
  contre le train/serve skew : **aucune feature ne se calcule ailleurs**, ni
  dans `build_dataset.py`, ni dans `daily.py`, ni dans `backfill.py`.
- Chemin `tide` = la branche `models is None`. Les colonnes vivent dans
  `FEATURE_COLUMNS`. **Ne touche pas** `model_feature_columns` / les chemins
  `wave` et `wind` : hors périmètre, et ça obligerait à ré-entraîner 6 stations.
- `train.py --ablate <colonnes>` met les colonnes à zéro et **ré-entraîne** :
  mêmes lignes, même split, même graine, même capacité. C'est l'instrument de
  mesure, et le seul qui compte.
- `ABLATABLE` est dérivé des listes de colonnes : les nouvelles colonnes y
  entrent toutes seules.
- `scripts/build_dataset.py --kind tide` **réassemble les features** dans
  `data_train/<station>.parquet`. Un ajout de colonne impose donc un rebuild.

### Les trois pièges, nommés d'avance

- **Piège n°1 — le rebuild est le poste long.** REFMAR (~160 Mo chunké) +
  `harmonic.causal_predict` (un `utide.solve` tous les `REFIT_DAYS` sur 1825 j,
  ~50 s le fit) + Previous Runs API. Compte en dizaines de minutes par station.
  **Implémente donc les trois features d'abord, rebuild UNE fois**, puis mesure
  chaque feature par ablation sur ce dataset-là. Ne rebuild pas par feature.
- **Piège n°2 — la pression est servie stratifiée par âge de run.**
  `forcing_at_issue(forcing, t0)` rend une frame dont les lignes viennent de
  runs *différents* selon le jour d'échéance. Un `.diff()` appliqué **après** ce
  narrowing différencie donc à cheval sur deux runs aux frontières de jour :
  c'est un artefact de run, pas une tendance météo. La dérivée se prend
  **à l'intérieur d'un bloc `_d{day}`, avant le narrowing** — ou par toute autre
  construction que tu peux démontrer équivalente. **Un test doit échouer si
  quelqu'un remet le diff après narrowing** : c'est exactement la famille de
  skew que ce projet paie en boucle.
- **Piège n°3 — les tests à historique constant ne prouvent rien.** La leçon de
  `bf60c04` : tous les tests de `_mean_err` utilisaient un historique constant,
  où les trois fenêtres coïncident — inverser 3 h et 6 h les passait tous. Tes
  fixtures doivent **distinguer** : une dérivée de baseline qui change de signe,
  un vent où u et v diffèrent, une pression avec des pentes 3 h et 6 h
  différentes.

---

## Contraintes dures

1. **Une piste n'est acquise que mesurée.** Fenêtre et split identiques, via
   `--ablate`. Un gain plausible n'est pas un gain. Trois itérations ont déjà
   été dépensées à retirer des gains qui n'en étaient pas.
2. **Mesurer la quantité publiée**, jamais un proxy en amont : le gain hors
   biais après le modèle ML, pas un score sur la baseline seule ni une
   corrélation avec le résidu.
3. **Ne touche pas au gate.** `GATE`, `TEST_DAYS_BY_KIND`, `VAL_DAYS_CAP`,
   `FIT_LOOKBACK_DAYS`, `REFIT_DAYS`, `trend=False` : tout reste tel quel.
   Déplacer les poteaux après le tir est ce que ce projet reproche aux autres.
4. **Pas de liste de features par station.** Les deux stations reçoivent les
   mêmes colonnes ; qu'une seule s'en serve est un résultat, pas une config.
5. Pas de nouvelle dépendance, pas de nouvelle source de données, pas de
   nouveau module. Les trois features se calculent sur des données déjà en main.
6. **Ne publie rien, ne push rien.** Pas de `daily`, pas de `backfill`, pas de
   `.github/`, pas de `data/`. Branche `feat/tide-features` à partir de `main`,
   commits locaux uniquement.
7. Avant chaque commit de code : `/simplify` sur les changements puis appliquer
   les corrections (`CLAUDE.md`).
8. Prose et messages de commit en **français**, du registre du dépôt : ce qui a
   été mesuré, ce qui reste faux, jamais de superlatif.

---

## Protocole imposé

### Phase 0 — état de départ
`git status` propre, branche créée. Lance `uv run pytest` et note le vert de
départ. Relis `features.py`, `dataset.py`, `scripts/build_dataset.py`,
`scripts/train.py`, et le commit `bf60c04` (le patron d'un ajout de feature
réussi, tests et doc compris).

### Phase 1 — implémentation des 3 features + tests unitaires
Les trois colonnes dans `build_features`, chemin `tide` seul. Un test par
feature dans `tests/test_features.py`, plus le test anti-régression du piège
n°2. `uv run pytest` vert, `uv run ruff check` propre.

### Phase 2 — rebuild unique
`uv run python scripts/build_dataset.py --kind tide --station brest,saint-malo`
(vérifie les flags réels). **En arrière-plan**, c'est long. Vérifie ensuite que
les parquets portent bien les nouvelles colonnes et que le nombre de lignes n'a
pas bougé de façon inexpliquée — une chute de lignes signifie un `SourceError`
de couverture déclenché par ta feature, à diagnostiquer avant toute mesure.

### Phase 3 — mesures
Référence : `train.py` sans ablation (les 3 features actives). Puis une ablation
par feature, plus les combinaisons utiles :

| mesure | `--ablate` |
|---|---|
| référence | *(rien)* |
| feature 1 | `tide_rate` (nomme comme tu veux, sois cohérent) |
| feature 2 | `wind_stress_u,wind_stress_v` |
| feature 3 | `dp_dt_3h,dp_dt_6h` |
| les 3 | tout ce qui précède |

Pour chaque feature retenue, un **intervalle de confiance par bootstrap sur les
jours d'émission** (méthode de `bf60c04`, 2000 tirages) : sans ça un écart de 2
points n'est pas distinguable d'un tirage. Le script de bootstrap va dans le
scratchpad ; ne le commite que s'il tient en ~60 lignes et sert les 3 mesures.

Question n°2 à trancher **sur les chiffres, pas en prose** : garder `wind_u10/v10`
**et** la tension, ou remplacer. Règle : on ne remplace que si l'ablation montre
que la paire brute ne porte plus rien une fois la tension présente (IC95 % de sa
contribution contenant zéro aux deux stations). Sinon on garde les deux.

### Phase 4 — décision, feature par feature

**Garder** si, sur le gain hors biais :
- au moins une station gagne avec un **IC95 % strictement au-dessus de zéro**, et
- aucune station ne perd avec un IC95 % strictement au-dessous de zéro, et
- aucune station ne passe sous le gate.

**Jeter** sinon — et jeter veut dire **retirer le code**, pas le laisser
« au cas où ». Une feature jetée laisse une trace dans la doc (chiffres + IC),
jamais dans `features.py`.

Une feature gagnante à une seule station se garde quand même (cas `mean_err_3h/6h` :
saint-malo +3,5 %, brest −0,4 % indistinguable de zéro) — contrainte dure n°4.

### Phase 5 — artefacts
Ré-entraîner brest et saint-malo dans la configuration retenue, artefacts et
`gate.json` écrits. **Les deux stations doivent rester PASS.** Si l'une tombe
sous le gate, reviens à la configuration retenue précédente et documente-le : on
ne publie pas une régression pour garder une feature.

### Phase 6 — doc
- `docs/plan-dev-modele.md` : les features traitées passent de « à tester » à
  une section « fait », avec les tableaux de chiffres, les IC, et les réserves
  honnêtes (ce qui n'a **pas** été mesuré compte autant que le reste).
  Mets à jour « Réserves ouvertes » — notamment le plafond saint-malo, que la
  feature 1 vise directement : dis s'il bouge, de combien, et ce qu'il en reste.
- `docs/model-eval.md` : les chiffres publiés des deux stations.
- `README.md` : uniquement si la table des stations ou un chiffre cité change.
- Ne réécris pas l'historique des sections datées : on ajoute, on ne repeint pas.

### Phase 7 — handoff
Invoque le skill `handoff-matt`. Le handoff doit contenir, dans cet ordre :
ce qui est gardé et pourquoi (chiffres + IC), ce qui est jeté et pourquoi,
ce qui n'a pas été mesuré, l'état du gate, et la prochaine action évidente.

---

## Sortie attendue

- Branche `feat/tide-features`, un commit par feature retenue (+ un commit doc),
  message français long à la manière de `bf60c04`, terminé par
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- `uv run pytest` vert, `uv run ruff check` propre.
- `pipeline/models/gate.json` : brest et saint-malo `pass: true`.
- Docs à jour (phase 6) et handoff (phase 7).
- Un rapport final en réponse : tableau `feature × station × gain hors biais ×
  IC95 % × verdict`, puis les décisions et les réserves. Pas de prose de
  célébration.

## Garde-fous d'arrêt — arrête-toi et rends la main si

- le rebuild de phase 2 échoue ou perd plus de 5 % des lignes sans cause
  identifiée ;
- une API (REFMAR, Open-Meteo Previous Runs) est indisponible ou dégradée : ne
  substitue **aucune** autre source, c'est le skew que ce chantier existe pour
  éviter ;
- une mesure impose de toucher au gate, au split, ou à la profondeur de fit pour
  passer ;
- les 3 features sont jetées : c'est un résultat valide, commite les tests et la
  doc, et dis-le franchement ;
- tu dépasses ~4 h de travail machine : rends l'état, ce qui est mesuré, ce qui
  ne l'est pas.
