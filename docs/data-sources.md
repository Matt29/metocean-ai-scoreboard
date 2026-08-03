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

## 5. Résumé des stations retenues

Voir `pipeline/config/stations.toml` — 4 stations houle (Candhis) + 2
stations niveau d'eau (SHOM REFMAR), toutes vérifiées vivantes le
2026-07-30.

| id | kind | source | source_id | zone |
|---|---|---|---|---|
| pierres-noires | wave | candhis | 02911 | Iroise (Bretagne) |
| belle-ile | wave | candhis | 05602 | Bretagne sud |
| anglet | wave | candhis | 06402 | Gascogne |
| cherbourg | wave | candhis | 05008 | Manche |
| brest | tide | shom | 3 | Iroise (Bretagne) |
| saint-malo | tide | shom | 410 | Manche |

## Points ouverts / non bloquants

- ~~Heure exacte de disponibilité du run MFWAM quotidien~~ : **sans objet
  depuis Task 7** — CMEMS/MFWAM retiré, la baseline vague vient d'Open-Meteo
  Marine (§ 4ter), pas d'un run CMEMS à surveiller.
- ~~Profondeur d'archive REFMAR au-delà de ~3 mois~~ : **résolu en Task 6** —
  `sources=1` sert **365 jours continus** (8761 valeurs horaires pour Brest et
  Saint-Malo, 2025-07-30 → 2026-07-30), en requêtes de 30 jours (cap API de
  31 jours géré par `fetch_tide_obs(date_end=...)`).
- IOC : fallback documenté seulement, pas de fetcher prévu en v1.
