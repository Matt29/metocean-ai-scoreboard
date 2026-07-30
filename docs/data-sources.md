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

## 4. Copernicus Marine (CMEMS) — MFWAM, baseline officielle Hs

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

- Heure exacte de disponibilité du run MFWAM quotidien : à reconfirmer avant
  de figer le cron GitHub Actions (voir section 4).
- Profondeur d'archive REFMAR au-delà de ~3 mois : non caractérisée
  précisément (non requis par la brief, à vérifier si l'entraînement a besoin
  de plus d'historique niveau d'eau que l'archive brute n'en offre).
- IOC : fallback documenté seulement, pas de fetcher prévu en v1.
