# Sources de données — spike de validation (2026-07-30)

Toutes les vérifications ci-dessous sont des appels API réels effectués le
2026-07-30 (heure de référence UTC ~08:10-08:20). Aucune clé secrète n'est
présente dans ce document ou dans `pipeline/config/stations.toml`.

## 1. Candhis (Cerema) — houle (Hs), 4 bouées, source primaire "wave"

- **Base URL :** `https://candhis.cerema.fr/API/v1/`
- **Auth :** header `Authorization: <CANDHIS_API_KEY>` (clé dans `.env` local,
  non commitée ; en prod : secret GitHub Actions `CANDHIS_API_KEY`).
- **Format :** JSON `{success, nbLig, entete, results}` (results = liste de
  lignes, colonnes décrites dans `entete`).
- **Endpoints utilisés :**
  - `getCampListe.php` → liste des 124 campagnes, colonnes
    `["Code campagne","Nom","Actif","Type données TR"]`.
  - `getCampInfos.php?camp=<id>` → coordonnées, colonnes
    `["Nom","Latitude","Longitude","Profondeur","Directionnel","Capteur"]`.
  - `getCampTR.php?camp=<id>&dateDeb=<date>` → séries temps réel 30 min,
    colonnes `["Date","H1/3 (m)","Hmax (m)","TH1/3 (s)","Dir. au pic (°)",
    "Etal. au pic (°)","Temp. mer (°C)"]`.
  - `getCampTD.php` (non appelé — voir décision archive ci-dessous).

### Sélection des bouées (Step 1)

`getCampListe.php` → 124 campagnes, 40 `Actif == "1"`. Filtrées sur les zones
cibles (Iroise/Bretagne, Gascogne, Manche), puis chacune vérifiée avec
`getCampTR.php?camp=<id>&dateDeb=2026-07-23` (J-7) :

| camp | nom | zone | lignes / 7j | 1ère date | dernière date |
|---|---|---|---|---|---|
| 02911 | Les Pierres Noires | Iroise (Bretagne) | 353 | 2026-07-23 00:00 | 2026-07-30 08:00 |
| 02922 | Ile de Batz | Bretagne nord (non retenue, redondante avec 02911) | 353 | 2026-07-23 00:00 | 2026-07-30 08:00 |
| 05602 | Belle-Ile | Bretagne sud | 353 | 2026-07-23 00:00 | 2026-07-30 08:00 |
| 06402 | Anglet | Gascogne | 353 | 2026-07-23 00:00 | 2026-07-30 08:00 |
| 05008 | Cherbourg (extérieur) | Manche | 318 | 2026-07-23 00:00 | 2026-07-30 07:30 |

Toutes ≥ 300 lignes/7j → fraîches. **Retenues (4, une par zone) :**
Pierres Noires (02911), Belle-Ile (05602), Anglet (06402), Cherbourg
extérieur (05008). Ile de Batz écartée (doublon géographique avec Pierres
Noires, pas de valeur ajoutée en v1).

Coordonnées via `getCampInfos.php` :

| camp | nom | lat | lon |
|---|---|---|---|
| 02911 | Les Pierres Noires | 48.2903328 | -4.9683332 |
| 05602 | Belle-Ile | 47.2849998 | -3.2850001 |
| 06402 | Anglet | 43.5321655 | -1.6150000 |
| 05008 | Cherbourg (extérieur) | 49.6940600 | -1.6213680 |

### Fuseau horaire

Comparé le dernier timestamp Candhis TR (`2026-07-30 08:00`/`08:10`) à l'heure
UTC réelle au moment de l'appel (`date -u` → `2026-07-30 08:10 UTC`) :
concordance à quelques minutes près. **Les dates Candhis TR sont en UTC.**

### Archive TR (Step 2)

`getCampTR.php?camp=02911&dateDeb=2025-07-30` (J-365) → `nbLig=16806`,
première date `2025-07-30 00:00`, dernière `2026-07-29 23:30`. **TR sert au
moins 365 jours d'historique en continu** (pas de trou constaté).

**Décision :** TR couvre déjà ≥ 12 mois → pas besoin d'interroger `getCampTD.php`
pour l'entraînement (condition de la brief "si < 12 mois, tester TD" non
remplie). `getCampTD.php` reste une option si un historique plus profond est
un jour nécessaire (doc :
`https://candhis.cerema.fr/doc/04_Candhis_API_v1_Utilisateur.pdf`).

### Quota

Non documenté précisément par l'API ; HTTP 429 constaté en cas de dépassement
(comportement rapporté par le contrôleur, pas re-testé ici pour ne pas
consommer le quota). Cette session a utilisé : 1× `getCampListe`,
5× `getCampTR` (dateDeb J-7), 1× `getCampTR` (dateDeb J-365), 4×
`getCampInfos` = 11 appels. Rester frugal en usage quotidien (1 appel TR par
bouée par jour dans le pipeline).

### Correction du 2026-08-05 — le plafond réel d'une requête TR

**L'affirmation ci-dessus (« TR sert au moins 365 jours d'historique en
continu ») était trompeuse par construction de son propre test** : le spike du
2026-07-30 avait interrogé `dateDeb=J-365`, donc une fenêtre qui se terminait
tout juste à « aujourd'hui » — impossible d'y voir un plafond qui coupe *avant*
aujourd'hui.

Constaté en vrai le 2026-08-05, en reconstruisant les datasets d'entraînement
(`build_dataset.py`, fenêtre 2023-08-06 → 2026-08-05) : `getCampTR.php?dateDeb=2021-08-06`
renvoie des lignes de **2021-08-25 à 2022-08-05** et rien de plus récent, alors
que la requête est faite en 2026. Chaque réponse est plafonnée à **~365 jours à
partir de `dateDeb`**, jamais jusqu'à « maintenant » — quel que soit l'âge de
`dateDeb`. Silencieux : `success=true`, pas d'erreur, pas de champ signalant la
troncature. C'est ce qui expliquait l'absence des `*_raw.parquet` houle avant ce
correctif.

**Chaînage mis en place** (`sources/candhis.fetch_wave_obs`, même motif que
`waterlevel.fetch_tide_obs` pour SHOM/REFMAR, limitée elle à 31 jours par
appel) : une nouvelle requête est envoyée, ancrée juste après la dernière
observation reçue, **uniquement quand la réponse précédente a réellement buté
sur le plafond** (dernière date à moins de 2 jours de `dateDeb + 365 j`). Une
fenêtre courte — l'usage quotidien réel du pipeline, `daily.py`/`backfill.py`,
qui ne remonte que de quelques jours — ne déclenche jamais de second appel et
coûte donc toujours exactement 1 requête, comme documenté ci-dessus.

**Garde-fou ajouté dans `build_dataset.py`** : si les observations d'une
station s'arrêtent plus de 7 jours avant la fin de la fenêtre demandée, le
script échoue bruyamment (`RuntimeError`) plutôt que d'écrire un dataset
silencieusement amputé de ses mois les plus récents — c'est exactement le mode
de panne que ce plafond provoque sans lui.

## 2. SHOM REFMAR — niveau d'eau, source primaire "tide" (remplace IOC)

Source changée en cours de tâche sur instruction du coordinateur : REFMAR
(service officiel SHOM pour les marégraphes RONIM) devient la source
primaire pour le niveau d'eau, IOC passe en fallback documenté (non implémenté
dans `stations.toml`).

- **Base URL :** `https://services.data.shom.fr/maregraphie`
- **Auth :** **aucune** — endpoints publics, pas de clé requise (vérifié par
  appel direct sans header d'auth, HTTP 200). La démarche "demande de clé
  SHOM (data.shom.fr)" mentionnée dans la brief initiale est donc **sans
  objet** pour REFMAR : le service utilisé ne la requiert pas.
- **Doc :** `https://services.data.shom.fr/support/en/services/refmar`
  (Swagger UI ; spec OpenAPI récupérée à
  `https://services.data.shom.fr/support/sites/default/files/2026-03/service_refmar_en.yaml`).
- **Format :** JSON `{"data": [{"idstation", "idsource", "value", "timestamp"}]}`
  (`value` = hauteur d'eau en mètres, `timestamp` en UTC).
- **Endpoints utilisés :**
  - `GET /service/tidegauges` → liste des 176 marégraphes (`shom_id`, `name`,
    `longitude`, `latitude`, `state`, `reseau`).
  - `GET /observation/{format}/{shom_id}?sources=<n>&dtStart=<ISO>&dtEnd=<ISO>&interval=<min>`
    → observations. `sources` : 1 = brut haute fréquence, 2 = brut différé,
    3 = validé différé, 4 = validé horaire, 5 = brut horaire. Plage
    `dtStart`/`dtEnd` **limitée à 31 jours** par appel.

### Identification des stations (Step 3, remplace la recherche de codes IOC)

`GET /service/tidegauges` → recherche texte sur "BREST" / "MALO" :

| shom_id | name | lat | lon | state |
|---|---|---|---|---|
| 3 | BREST | 48.3829002 | -4.4950399 | OK |
| 410 | SAINT-MALO | 48.6408120 | -2.0281030 | OK |

### Validation observations (fraîcheur)

`GET /observation/json/3?sources=1&dtStart=2026-07-28T08:19Z&dtEnd=2026-07-30T08:19Z&interval=10`
→ 288 lignes, première `2026/07/28 08:20:00`, dernière `2026/07/30 08:10:00`
(≈ 9 min avant l'appel → quasi temps réel). Idem pour Saint-Malo (id 410) :
288 lignes, même fenêtre.

**Note :** `sources=4` (validé horaire) a renvoyé `{"data":[]}` sur la même
fenêtre J-2/J — la donnée validée a un délai de publication non négligeable
(non chiffré précisément ici). **Décision : utiliser `sources=1` (brut haute
fréquence) pour le pipeline quotidien**, cohérent avec "obs de la veille"
dans le run — la donnée brute est disponible en quasi temps réel, la donnée
validée ne l'est pas. Si un post-traitement qualité est nécessaire plus tard,
recroiser avec `sources=3`/`4` en différé.

### Archive (sondage, hors périmètre strict de la brief)

`GET /observation/json/3?sources=1&dtStart=2026-05-01T00:00Z&dtEnd=2026-05-02T00:00Z&interval=60`
→ 25 lignes retournées sans erreur : au moins ~3 mois d'historique brut
disponibles. Profondeur exacte non caractérisée (non nécessaire pour ce
spike ; à creuser en Task 2/entraînement si besoin d'un historique plus
long — combiner avec un hindcast de niveau d'eau si l'archive brute est trop
courte).

### Quota

Non documenté dans le Swagger ; aucune limitation rencontrée sur ~6 appels
successifs sans clé. À surveiller en usage quotidien (1 appel/station/jour,
charge négligeable).

### Décision

**Source primaire = SHOM REFMAR**, sans authentification, endpoints stables
et documentés. IOC reste en fallback documenté seulement (voir section 3) —
non câblé dans `stations.toml` pour l'instant, car REFMAR fonctionne et
couvre exactement les stations visées avec une meilleure fraîcheur
(10 min vs Brest/Saint-Malo IOC également ~temps réel, mais REFMAR est la
source officielle française, plus pertinente pour un produit "vs modèle
officiel français").

## 3. IOC Sea Level Monitoring — fallback documenté (non utilisé en v1)

Conservé comme redondance possible si REFMAR devient indisponible.

- **Base URL :** `https://www.ioc-sealevelmonitoring.org/service.php`
- **Auth :** aucune.
- **Format :** JSON. `query=stationlist&format=json` → liste à plat (1968
  stations mondiales) ; **le paramètre `country` ne filtre pas côté serveur**
  (vérifié : toutes les stations sont renvoyées quel que soit `country`) — il
  faut filtrer côté client sur le champ `country == "FRA"` (150 stations
  françaises trouvées).
- **Codes confirmés pour Brest/Saint-Malo** (trouvés en filtrant `Location`) :
  - `bres` → Brest, lat 48.38, lon -4.5
  - `stma` → Saint-Malo, lat 48.6416, lon -2.028
  - (les codes devinés initialement, `bres`/`smal`, étaient partiellement
    faux : `smal` n'existe pas, c'est `stma`.)
- `query=data&code=bres&timestart=2026-07-28&format=json` → 3323 points,
  résolution ~1 min, dernier point `2026-07-30 08:04:00`. Idem `stma` → 3329
  points, dernier `2026-07-30 08:04:00`. **Fonctionne, données fraîches.**
- **Quota :** non documenté, aucune limite rencontrée.
- **Décision :** source de secours uniquement si REFMAR tombe en panne
  (source différente, réseau différent — bonne redondance). Pas implémenté
  dans le pipeline v1 (pas de fetcher IOC prévu tant que REFMAR fonctionne).

## 4. Copernicus Marine (CMEMS) — MFWAM, baseline officielle Hs [historique, retiré Task 7]

> **Section conservée comme trace du spike, plus utilisée.** Le retrain
> multi-modèles (2026-08) a retiré CMEMS/MFWAM du pipeline : la baseline vague
> vient désormais d'Open-Meteo Marine (voir § 4ter). `pipeline/src/scoreboard/sources/mfwam.py`
> et la dépendance `copernicusmarine` ont été supprimés.

- **Dataset :** `cmems_mod_glo_wav_anfc_0.083deg_PT3H-i`, variable `VHM0`.
- **Auth :** identifiants CMEMS déjà configurés (`~/.copernicusmarine-credentials`,
  compte existant issu d'autres projets OCEANO).
- **Outil :** `uvx copernicusmarine subset` (aucune install locale requise).
- **Commande testée** (zone Pierres Noires, aujourd'hui → J+2) :
  ```
  uvx copernicusmarine subset --dataset-id cmems_mod_glo_wav_anfc_0.083deg_PT3H-i \
    --variable VHM0 --start-datetime 2026-07-30T00:00:00 \
    --end-datetime 2026-08-01T00:00:00 \
    --minimum-longitude -5.1 --maximum-longitude -4.9 \
    --minimum-latitude 48.2 --maximum-latitude 48.4 \
    -o <dir> -f test_mfwam.nc
  ```
  → succès, `"status":"000"`, fichier `.nc` téléchargé (13.5 KB).
- **Contenu vérifié :** 17 pas de temps, résolution 3h, de `2026-07-30 00:00`
  à `2026-08-01 00:00` → **couvre bien +48h** à partir de minuit UTC du jour
  courant.
- **Fréquence de run :** métadonnées du produit (`copernicusmarine describe`)
  indiquent "analysis 4 times a day, and a forecast of 10 days at 0:00 UTC" —
  largement suffisant pour l'horizon +48h du produit. Le run du jour J était
  déjà disponible et complet à **08:12 UTC** au moment du test (impossible de
  dater plus précisément l'heure de mise à disposition sans répéter le test
  sur plusieurs jours, hors périmètre de ce spike).
- **Conséquence pour le cron :** la brief/le spec supposent un cron ~06h UTC.
  Ce test confirme seulement que les données sont là à 08:12 UTC, pas
  qu'elles le sont dès 06h. **Point d'attention pour Task suivante** :
  vérifier la disponibilité effective à 06h UTC avant de figer l'heure du
  cron (ou ajouter une marge / un retry si le run n'est pas encore publié).
- **Décision :** MFWAM accessible et exploitable tel quel comme baseline Hs.

## 4bis. Open-Meteo — vent 10 m (feature de forçage atmosphérique)

Ajouté en Task 7B. Sources tranchées dans
`.superpowers/sdd/2026-07-30-scoreboard-metocean-ia/spike-wind-sources.md` :
Copernicus Marine n'expose aucune variable vent sur le dataset MFWAM utilisé, et
l'API ARPEGE de Météo-France n'a qu'une archive roulante ~14 jours — inutilisable
pour l'entraînement. Un seul fournisseur retenu, JSON pur, aucune dépendance
GRIB2/cfgrib.

- **Entraînement (ERA5, réanalyse)** :
  `https://archive-api.open-meteo.com/v1/archive`
  `?latitude=&longitude=&start_date=&end_date=`
  `&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms&timezone=UTC`
  → **une seule requête par station** pour toute la fenêtre (365 j vérifiés).
- **Inférence +48 h (ARPEGE Europe, prévision)** :
  `https://api.open-meteo.com/v1/forecast`
  `?...&models=meteofrance_arpege_europe&forecast_days=3&wind_speed_unit=ms&timezone=UTC`
- **Pas de clé d'API** sur l'offre gratuite. Implémentation :
  `pipeline/src/scoreboard/sources/wind.py` (`fetch_wind_history` /
  `fetch_wind_forecast`, erreur réseau ou payload invalide → `SourceError`).
- **Convention** : Open-Meteo renvoie une direction *météorologique* (d'où vient
  le vent). Le fetcher convertit une fois pour toutes en composantes
  `u10 = -V·sin(dir)` (vers l'est) et `v10 = -V·cos(dir)` (vers le nord), en m/s.
  Une direction en degrés est circulaire et inutilisable comme feature brute.
- **Pression au niveau de la mer — testée et écartée.** `pressure_msl` est servie
  par ces deux mêmes endpoints, dans la même réponse, donc à coût réseau nul ;
  elle a été ajoutée puis mesurée en Task 7C comme anomalie à 1013,25 hPa, et
  **retirée : elle dégrade 5 stations sur 6**. Le tableau des Δ est dans
  `docs/model-eval.md` (« Pistes testées et écartées »). À ne pas re-tenter sans
  élément nouveau — un historique d'entraînement nettement plus long, par
  exemple.

### Attribution (obligatoire)

> Weather data by [Open-Meteo.com](https://open-meteo.com) — ERA5 reanalysis
> (Copernicus Climate Change Service / ECMWF) and Météo-France ARPEGE, served
> under CC BY 4.0.

À afficher sur le scoreboard public (Task 9) au même titre que les attributions
Candhis / SHOM / CMEMS.

### Contrainte non commerciale — et porte de sortie

L'offre gratuite Open-Meteo est réservée à un **usage non commercial**
(< 10 000 requêtes/jour). Décision utilisateur : usage non commercial assumé
pour ce projet (scoreboard public, sans publicité ni paywall). **Si le projet se
monétise, cette source devient non conforme** ; deux portes de sortie, aucune ne
change les features :

1. Souscrire l'offre payante Open-Meteo (mêmes endpoints, clé d'API en `.env` +
   secret GitHub Actions) ;
2. Basculer sur les sources primaires : ERA5 direct via CDS (`cdsapi`) pour
   l'entraînement, ARPEGE direct via l'API WCS de Météo-France pour l'inférence
   — même profil de skew, mais dépendances GRIB2/`cfgrib` et file d'attente CDS
   en plus.

### Skew train/serve ERA5 → ARPEGE (à ne pas minimiser)

Le modèle est **entraîné sur une réanalyse ERA5** (0,25°, ECMWF, vent « connu
après coup ») et **servi avec une prévision ARPEGE Europe** (0,1°, Météo-France,
vent prévu à +1…+48 h). Ce sont deux familles de modèles différentes sur deux
grilles différentes, et la prévision porte une erreur de lead time que la
réanalyse n'a pas. **Ce n'est pas une équivalence.**

C'était exactement le même type de compromis que celui documenté jusqu'à
Task 6 pour la baseline vague (l'**analyse** MFWAM CMEMS servait de proxy à la
prévision archivée, faute d'archive libre) : un skew de type biais moyen,
borné et explicable, sur une feature correctrice et non sur le signal principal.
Pour le **vent**, il reste ouvert et se résorbera de la même façon : une fois
que le run quotidien aura accumulé ~6–12 mois de ses **propres** prévisions
ARPEGE (voir « Archive du vent servi » ci-dessous), on ré-entraînera dessus et
le skew disparaîtra. En attendant, les chiffres de `docs/model-eval.md`
doivent se lire « vent parfait à l'entraînement », donc plutôt optimistes.

**Résolu pour les vagues par le retrain 2026-08 (Task 7)** : le chemin vague
n'a plus cette forme de skew du tout, il a été retiré à la source plutôt que
résorbé — voir § 4ter. Open-Meteo Marine sert le même contrat JSON en train
(Historical Forecast API) et en serve (Marine API), donc plus d'analyse
utilisée comme proxy de prévision.

### Archive du vent servi (Task A1) — corpus pour un ré-entraînement honnête

Depuis le run du **2026-08-03**, `daily.py` archive dans
`pipeline/data_forecast_archive/` (un fichier Parquet par jour d'émission,
`YYYY-MM-DD.parquet`, **commité**) la prévision ARPEGE effectivement servie à
l'inférence pour chaque station qui a publié ce jour-là : une ligne par
`(station_id, valid_time)` avec `issued`, `lead_h`, les colonnes de forçage
dérivées de `wind.FORCING_COLUMNS` (`wind_u10`, `wind_v10`) et `source`
(`meteofrance_arpege_europe`, l'identifiant du modèle Open-Meteo servi — la
colonne qui permettra de basculer un jour vers `meteodata_hub`/AROME sans
mélanger deux modèles dans un même corpus). Aucune requête réseau
supplémentaire : la donnée est déjà en mémoire dans le run, seulement
conservée plutôt que jetée.

C'est le seul moyen de supprimer (pas réduire) le skew ERA5-train/ARPEGE-serve
documenté ci-dessus : une fois ~6–12 mois de vraies prévisions accumulées, un
ré-entraînement sur ce corpus n'aura plus à deviner l'erreur de lead time
ARPEGE via un proxy. Une station en échec d'inférence n'archive rien ce
jour-là (pas de ligne inventée) ; un run `--dry-run` n'écrit jamais dans ce
répertoire (même logique que pour `data/`).

Limite connue : `issued` est le `t0` nominal du scoreboard (date du run,
06:00 UTC) — cohérent avec la définition de `lead_h` partout ailleurs dans ce
pipeline — et non l'heure d'initialisation réelle du run ARPEGE sous-jacent.
Open-Meteo n'expose pas proprement la référence du run servi, donc le corpus
ne peut pas distinguer un run 06Z frais d'un run 00Z vieux de six heures :
facteur de confusion réel pour une analyse de l'erreur en fonction de
l'échéance, à garder en tête pour un futur ré-entraînement.

## 4ter. Open-Meteo — vagues, retrait CMEMS (Task 7, retrain 2026-08)

Remplace intégralement la section 4 (CMEMS/MFWAM). Sondage de couverture et
comparaison faits en Task 0, rapport complet dans
`.superpowers/sdd/2026-08-03-retrain-multi-modeles/task-0-coverage.md`.

- **Entraînement et inférence +48 h (Marine API, un seul et même chemin)** :
  `https://marine-api.open-meteo.com/v1/marine`
  `?...&hourly=wave_height&models=meteofrance_wave,ecmwf_wam025,gwam,ewam,ncep_gfswave025`,
  en mode archive (`start_date`/`end_date`) pour l'entraînement, en mode
  prévision pour l'inférence — même URL, même contrat JSON, même parseur
  (`pipeline/src/scoreboard/sources/marine.py`, `fetch_wave_models_history` /
  `fetch_wave_models_forecast`). **Pas de skew train/serve de type « famille de
  modèle différente »** pour la houle, contrairement au vent (§ 4bis) où
  l'entraînement passe par `historical-forecast-api.open-meteo.com` (les 3
  modèles de vent candidats, `fetch_wind_models_history`) pour approcher au
  plus près la prévision ARPEGE servie en production.
- **Couverture mesurée (Task 0), fenêtre retenue `2025-06-01` → hier** : les 5
  modèles de vagues passent tous ≥ 90 % de couverture non-null sur les 4
  stations. `gwam` reste à 98,8 % (démarrage réel `2025-06-06`, 5 jours après
  le début de fenêtre) et `ncep_gfswave025` était tardif à **Anglet**
  seulement (démarrage `2025-05-05`, avant `2025-05-01` initial testé) —
  aucun modèle exclu, juste un recul de la date de début par rapport à la
  fenêtre `2025-01-01` initialement envisagée.
- **Écart MFWAM CMEMS (baseline archivée) vs `meteofrance_wave` (Open-Meteo)**,
  comparé sur la fenêtre disponible `2026-07-04` → `2026-08-03` (720/720
  points horaires appariés par station) : MAE ≤ 5 cm et corrélation ≥ 0,998
  sur les 3 stations avec historique (`pierres-noires` 0,0056 m / 0,9998,
  `belle-ile` 0,0372 m / 0,9982, `anglet` 0,0478 m / 0,9991) — cohérent avec
  `meteofrance_wave` réexposant vraisemblablement le même modèle
  MFWAM/PREVIMER. `cherbourg` n'a pas d'historique archivé, donc pas de
  preuve de continuité pour cette station à ce stade.
- **Limite leads courts de l'archive** : `archive.write_day` (§ « Archive du
  vent servi » ci-dessus, étendue en Task 7 aux colonnes `hs_*` du chemin
  vague) n'archive que la prévision **effectivement servie** à l'inférence —
  un point par lead de l'issue du jour (`lead_h >= 1`, horizon +48 h), pas un
  hindcast complet du run. Même limite que pour le vent : `issued` est le
  `t0` nominal du scoreboard, pas l'heure d'initialisation réelle du run
  vague sous-jacent.

## 4quater. Météo-France DPObs `/bouees` — archive d'observations bouées (2026-08-03)

Collecteur : `sources/mfbuoy.py` + `archive.write_obs_days`, commande
`uv run scoreboard archive-obs`, branchée **avant** le scoreboard quotidien afin
qu'une panne ultérieure ne fasse jamais perdre la fenêtre périssable. Sorties :
`pipeline/data_obs_archive/<jour>.parquet`, `data/buoys.json` et, depuis le
2026-08-29, `data/buoys/<wmo>/latest.json` + `history.json` (30 jours compacts),
committés ensemble puisque Actions est sans état.

`data/buoys.json` est un snapshot des positions renvoyées par l'API au dernier
collectage : une bouée peut donc bouger. La position du pilote Gascogne dans
`stations.toml` est alignée sur ce snapshot (45,21° N, 4,99° O) et reste une
référence explicite du dataset/modèle ; tout déplacement opérationnel notable
doit être revu avant de modifier cette référence, plutôt que propagé silencieusement.

**Ce que ça fait, et ce que ça ne fait pas.** Ça archive et publie les
observations pour que le compteur des ~2-3 mois d'historique nécessaires au
premier entraînement Méditerranée démarre (demande produit 4). Gascogne est
désormais déclarée dans `stations.toml` comme pilote `active = false` : le
dispatch quotidien et le builder savent lire `mfbuoy`, mais aucun gate, verdict
ou modèle n'est inventé avant l'entraînement. Les huit autres bouées restent des
observations publiques, pas des stations scorées.

**Rétention réelle : ~96 h, pas 24 h.** La doc Confluence annonce 24 h. Mesuré
le 2026-08-03 sur la requête exacte : `date_debut` à T-24/30/36/48/72/96 h
répond 200 avec une grille horaire complète (97 pas distincts à 96 h) ; à
T-120 h et au-delà, HTTP 400 « Contrôle de date en erreur ». Le collecteur
demande donc **90 h** (`mfbuoy.LOOKBACK_HOURS`), sous la limite dure, ce qui
laisse ~3,5 runs quotidiens de marge : un cron raté se rattrape tout seul au
lieu de laisser un trou définitif. La doc était fausse dans le sens favorable —
raison de plus pour mesurer plutôt que lire.

**Une requête par jour pour les 9 bouées** : `id_bouee` omis renvoie tout le
réseau (~713 lignes sur 90 h). Les positions viennent du payload
(`lat`/`lon`/`geo_id_wmo`/`name`), jamais en dur. Coût : 1 requête/jour contre
un palier de dizaines par minute.

**Idempotence par fusion sur (`geo_id_wmo`, `validity_time`)**, dernière
écriture gagnante — et non par remplacement des lignes d'une bouée comme
`archive.write_day`. La fenêtre du lendemain recouvre la fin de la veille : un
remplacement effacerait les heures que seul le run précédent avait vues.
Vérifié en réel (deux runs : 713 lignes, 0 doublon) et par test.

**Comptage non-null par bouée et par variable à chaque run** (sortie de la
commande) — un 200 OK ne prouve rien. Premier relevé, 2026-07-31 → 08-03 :

| Bouée | heures | haut_vag | per_moy_vag | dir_vag |
|---|---|---|---|---|
| BOUEE_AJACCIO | 80 | 80 | 80 | 80 |
| BOUEE_AZUR | 80 | 80 | 80 | 80 |
| BOUEE_CALVI | 84 | 84 | 84 | 84 |
| BOUEE_GASCOGNE | 84 | 84 | 84 | 84 |
| BOUEE_LION | 75 | 75 | 75 | 75 |
| BOUEE_PACA | 80 | 80 | 80 | 80 |
| BOUEE_PROVENCE | 79 | 79 | 79 | 79 |
| **BOUEE_SARDAIGNE** | 76 | **0** | **0** | **0** |
| BOUEE_VECCHIO | 75 | 75 | 75 | 75 |

**BOUEE_SARDAIGNE ne sert aucune donnée de vagues** sur les 90 h : la bouée est
vivante (`t`, `ff`, `dd`, `pmer`, `tmer` non-null sur ses 76 heures), c'est le
capteur de houle qui ne remonte rien. Le sondage Marine API la déclarait
ELIGIBLE — à juste titre, mais il portait sur la couverture *modèle* à sa
position, pas sur l'obs de la bouée. **8 bouées exploitables pour la houle, pas
9**, jusqu'à preuve du contraire ; le comptage quotidien tranchera si c'est une
panne ou un état permanent.

Valeurs archivées **brutes**, sans filtre de plausibilité (contrairement à
`candhis.fetch_wave_obs`) : ce corpus est la vérité terrain d'un futur
entraînement, le filtrage appartient à qui le consomme.

**Contrôle automatique depuis le 2026-08-29.** Une étape non bloquante du
workflow mesure la fraîcheur (acceptable entre 0 et 3 h) et, pour chaque bouée
ayant déjà fourni Hs, la complétude sur les dernières 24 h (seuil 80 %). Les IDs
wave sont l'union du catalogue et de toute preuve Hs dans l'archive complète :
une panne prolongée ne peut donc pas faire disparaître silencieusement les
bouées surveillées. Les écarts produisent des annotations GitHub et un tableau
dans `GITHUB_STEP_SUMMARY`, sans empêcher la sauvegarde des données brutes.

**État mesuré le 2026-08-29 sur `c2a5563`.** 29 partitions du 2026-07-31 au
2026-08-28, 5 682 lignes et 0 doublon sur
`(geo_id_wmo, validity_time)`. Sur les huit bouées wave, Hs est présent sur
100 % des lignes avant le 20 août et après le 25 août, mais seulement **37,2 %**
du 20 au 25 août : les lignes météo continuaient d'arriver avec les champs
vagues nuls. Gascogne reste complète sur ses 625 lignes ; Sardaign(e) reste à
0 Hs. Cette anomalie est une lacune amont conservée brute, pas un filtrage du
pipeline.

## 4quinquies. Météo-France DPObs + DPClim — vent aux stations terrestres (2026-08-04)

Collecteur : `sources/mfobs.py`. Deux APIs pour **la même mesure**, une par
usage : DPObs sert le scoring quotidien, DPClim sert l'entraînement. C'est la
source primaire du `kind = "wind"` (demande produit 3).

**Les deux APIs mesurent bien la même chose — c'est vérifié, pas supposé.**
Croisement DPObs temps réel / archive DPClim sur les 12 h communes à
Ouessant-Le Stiff le 2026-08-04 : **écart max 0,0 m/s**, directions
identiques. Une station de vent n'a donc **pas** le skew train/serve que porte
le forçage atmosphérique (§ 4bis : ERA5 à l'entraînement, ARPEGE au service).
Ne pas inventer de correction pour un biais qui n'existe pas.

### DPObs `/station/horaire` — le scoring quotidien

L'endpoint ne sert **qu'une heure par requête** — il n'accepte pas de plage, et
le paquet toutes-stations répond 404. D'où une boucle horaire, ~30 requêtes par
station et par run. Quota mesuré le 2026-08-04 : 90 requêtes d'affilée passent,
~130 non ; le collecteur se throttle à 1,5 s (`_MIN_INTERVAL_S`), soit une
marge volontairement large — un run quotidien n'est pas pressé, et une station
faussement « manquante » coûte une journée de scoreboard.

**C'est cette cadence, pas le TOML, qui plafonne le nombre de stations vent**
publiables : ~7,4 min de run pour 3 stations, linéaire ensuite.

Point de méthode payé en sondage : une hypothèse de seuil ne se réfute qu'au
delà du seuil. Les 90 requêtes passantes ont d'abord fait écarter à tort
l'hypothèse « quota » d'un échec survenu vers la 130ᵉ.

### DPClim — l'archive d'entraînement

Commande asynchrone (on commande, on attend, on récupère), avec **trois pièges
qui ont tous coûté un sondage** et que le docstring de `mfobs.py` répète au
plus près du code :

1. le fichier arrive en **HTTP 201**, pas 200 — une boucle d'attente qui ne
   guette que 200 jette la charge utile qu'elle attendait ;
2. il n'est **livré qu'une fois** (`410 production déjà livrée` ensuite) —
   d'où le cache disque écrit **avant** toute analyse. Ce cache est la seule
   copie durable, pas une optimisation ;
3. une commande couvre **un an au maximum**, d'où le découpage par année.

CSV à virgule décimale.

**Ce qui a débloqué le vent.** `docs/demandes-produit.md` § 3 plaçait le vent
derrière un préalable « archiver d'abord, entraîner dans 2-3 mois », hérité des
bouées (§ 4quater). C'était faux : le 403 initial mesurait un **abonnement**,
pas la disponibilité de la donnée. Le même producteur diffusait le même jeu par
DPClim (souscrit le jour même) et en fichiers ouverts sur data.gouv.fr, sans
clé. Entraînement fait le jour même sur 2,5 ans.

### Clés — deux, et non consolidées

`METEOFRANCE_DPCLIM_API_KEY` ouvre DPClim **et** DPObs ;
`METEOFRANCE_API_KEY` n'ouvre que DPObs. Chaque fonction demande celle dont
elle a besoin plutôt qu'une clé unique supposée tout couvrir : la couverture
d'une clé se mesure, elle ne se suppose pas. Les deux sont attendues par
`.github/workflows/daily.yml` — sans la clé DPClim, les stations vent tombent
en `missing`.

### Baseline et fenêtre d'entraînement

Baseline = meilleur des 3 modèles de vent Open-Meteo (`meteofrance_arpege_europe`,
`ecmwf_ifs025`, `icon_eu`), choisi par station comme pour la houle (§ 4ter).
Retenu sur les trois stations : `meteofrance_arpege_europe`.

Les 3 modèles ne sont servis **simultanément et sans trou** qu'à partir du
**2024-02-03** (`build_dataset.WIND_MODELS_START`) : avant, `ecmwf_ifs025` est
absent (0 % jusqu'en janvier 2024, 93 % en février), et rien du tout avant
2022. Démarrer plus tôt ne rallonge pas l'entraînement — cela fabrique des
émissions que le plancher de couverture de `features.py` rejette. **Ne pas
élargir cette borne sans re-sonder.**

Fenêtre effective : 2024-02-03 → veille, **21 936 h/station, obs à 100 %**.

### Les trois stations, une par critère, chacune justifiée par une mesure

| station | critère | mesure qui le justifie |
|---|---|---|
| `ouessant` (Le Stiff) | cap/île exposé | FF non-null 99,8 % sur 2020-2026, moyenne **7,75 m/s** — la plus ventée des trois |
| `dieppe` | site EMR | **8,6 km** du parc de Dieppe-Le Tréport, plus courte distance mesurée entre un poste ouvert et un parc éolien en mer français |
| `cherbourg-vent` (Homet) | co-localisée | **3,9 km** de la bouée houle `cherbourg` déjà publiée — le même point noté sur deux variables |

**C'est un proxy, et ça doit être dit ainsi.** Un anémomètre côtier à 10 m
au-dessus du *sol* n'est pas le vent au large. Dieppe est à 8,6 km du parc mais
à 40 m d'altitude et bien plus abritée (4,61 m/s de moyenne contre 7,75 à
Ouessant). Écrire « vent au parc » serait exactement le raccourci que ce projet
a passé trois itérations à débusquer ailleurs.

Valeurs filtrées comme `candhis.fetch_wave_obs` : un vent négatif ou au-delà de
75 m/s n'est pas une tempête, c'est un capteur en défaut.

## 5. Résumé des stations retenues

Voir `pipeline/config/stations.toml` — 4 stations houle (Candhis) + 2
stations niveau d'eau (SHOM REFMAR), toutes vérifiées vivantes le
2026-07-30, + 3 stations de vent (Météo-France DPObs/DPClim) ajoutées le
2026-08-04.

| id | kind | source | source_id | zone |
|---|---|---|---|---|
| pierres-noires | wave | candhis | 02911 | Iroise (Bretagne) |
| belle-ile | wave | candhis | 05602 | Bretagne sud |
| anglet | wave | candhis | 06402 | Gascogne |
| cherbourg | wave | candhis | 05008 | Manche |
| brest | tide | shom | 3 | Iroise (Bretagne) |
| saint-malo | tide | shom | 410 | Manche |
| ouessant | wind | mfobs | 29155005 | Iroise (Bretagne) |
| dieppe | wind | mfobs | 76217001 | Manche est (parc EMR) |
| cherbourg-vent | wind | mfobs | 50129001 | Manche |

## Points ouverts / non bloquants

- ~~Heure exacte de disponibilité du run MFWAM quotidien~~ : **sans objet
  depuis Task 7** — CMEMS/MFWAM retiré, la baseline vague vient d'Open-Meteo
  Marine (§ 4ter), pas d'un run CMEMS à surveiller.
- ~~Profondeur d'archive REFMAR au-delà de ~3 mois~~ : **résolu en Task 6** —
  `sources=1` sert **365 jours continus** (8761 valeurs horaires pour Brest et
  Saint-Malo, 2025-07-30 → 2026-07-30), en requêtes de 30 jours (cap API de
  31 jours géré par `fetch_tide_obs(date_end=...)`).
- IOC : fallback documenté seulement, pas de fetcher prévu en v1.
