# Scoreboard Metocean IA — Design v1

**Date :** 2026-07-30
**Statut :** approuvé (brainstorm du 2026-07-30)
**Repo :** `OCEANO/metocean-ai-scoreboard`

## Objectif

Produit d'appel public pour les services de Matthieu Caillaud (études metocean,
IA appliquée à l'océanographie). Une page web mise à jour quotidiennement où une
IA prédit les vagues et le niveau d'eau aux stations de référence françaises et
se fait noter publiquement contre le modèle physique officiel et contre les
observations. Diffusion : LinkedIn + lien depuis le site perso. Tracking
anonymisé des visites.

Critère de succès produit : le site tourne sans intervention pendant des mois,
le score glissant se raconte tout seul ("30 derniers jours : IA 0,14 m d'erreur,
modèle officiel 0,21 m"), et la page méthode convertit vers un contact.

## Périmètre v1

- **Variables :** Hs (hauteur significative) aux bouées Candhis + niveau d'eau
  aux marégraphes SHOM.
- **Stations :** 3-4 bouées Candhis (candidates : Pierres Noires, Anglet,
  Cherbourg — liste finale validée par la disponibilité effective des flux) +
  2 marégraphes SHOM (candidats : Brest, Saint-Malo).
- **Horizon de prédiction :** horaire, +24 h à +48 h, une émission par jour.
- **Baseline officielle :** MFWAM (Copernicus Marine) pour Hs ; prédiction de
  marée + surcote officielle pour le niveau d'eau.

Hors scope v1 : onglet super-résolution ERA5→côtier (volet 2 prévu), AIS,
autres variables, alerting, comptes utilisateurs.

## Modèle IA

- Un modèle par station, gradient boosting (LightGBM ou sklearn
  HistGradientBoosting) pour commencer.
- Entraînement hors-ligne sur l'historique : obs Candhis/SHOM + hindcast
  MFWAM/ERA5 + prévisions ARPEGE.
- Entrées en opérationnel : dernières observations, prévision officielle du
  jour, vent ARPEGE prévu → prédiction horaire +48 h.
- **Framing public honnête :** post-processing ML qui corrige le modèle
  physique. On ne prétend pas remplacer la physique ; on démontre qu'on
  l'améliore, mesurablement, chaque jour.
- Modèle versionné dans le repo ; remplaçable (chaque upgrade est un événement
  communicable).

## Architecture

Aucun serveur. Batch quotidien + site statique.

```
GitHub Actions (cron ~06h UTC)
  1. fetch obs de la veille (Candhis, SHOM)
  2. scoring des prédictions d'hier : erreur IA vs erreur baseline vs obs
     → métriques glissantes 7 j / 30 j / depuis le début
  3. fetch prévisions du jour (MFWAM via Copernicus Marine, ARPEGE)
  4. inférence IA → prédictions horaires +48 h par station
  5. écrit data/*.json + commit → push
        ↓
Vercel (Next.js) — redéploiement auto sur push, lit les JSON statiques
```

- `pipeline/` : Python géré par uv. Sous-modules : `sources/` (un fetcher par
  source de données), `scoring.py`, `predict.py`, `cli.py` (commandes `daily`,
  `backfill`, `--dry-run`).
- `web/` : Next.js. Trois écrans :
  1. **Scoreboard** (accueil) — tableau IA vs baseline par station, métriques
     glissantes. L'écran qu'on partage.
  2. **Page station** — séries temporelles obs / IA / baseline superposées
     (hier vérifié + demain prédit).
  3. **Méthode** — pitch technique, données utilisées, limites assumées,
     CTA contact/LinkedIn.
- Tracking : Vercel Analytics (+ Plausible optionnel). Anonymisé, sans bannière
  cookies. Pas d'identification nominative des visiteurs (non réaliste
  légalement).

## Contrats de données

- Les JSON publiés sont le seul contrat entre pipeline et web. Schéma par
  fichier : `data/stations.json` (métadonnées), `data/<station>/latest.json`
  (prédictions du jour), `data/<station>/history.json` (obs + prédictions
  passées + erreurs), `data/scores.json` (métriques agrégées).
- Les runs sont idempotents par date : relancer le job du jour J écrase les
  sorties du jour J sans dupliquer l'historique.

## Gestion d'erreurs

- Une source en panne ne bloque pas le run : le job publie ce qu'il a, la
  station concernée est marquée `"status": "missing"` pour la date, le site
  affiche "données manquantes".
- Rattrapage : commande `backfill --since <date>` qui rejoue les jours
  manquants (scoring compris).
- Échec total du job : le site continue de servir les derniers JSON commités —
  dégradation douce, jamais de page cassée.

## Tests

- pytest sur : parsers de chaque source (fixtures de réponses réelles
  enregistrées), logique de scoring (erreurs calculées sur cas connus),
  idempotence d'un run rejoué.
- Mode `--dry-run` du pipeline : tout exécuter sans commit.

## Risque n°1 — à lever avant toute autre tâche

Accès programmatique aux données :

1. **Obs Candhis (Cerema)** : clé API en main (stockée dans `.env` local, non
   commitée ; en production : secret GitHub Actions `CANDHIS_API_KEY`). Reste à
   valider le format du flux et la liste des bouées réellement servies.
2. **API SHOM (data.shom.fr)** : demander la clé gratuite, vérifier les quotas.
3. **Copernicus Marine / ARPEGE** : accès déjà maîtrisé (downloaders existants
   dans OCEANO/API_METEO_FRANCE et projets CMEMS).

La première tâche du plan d'implémentation est un spike de validation de ces
trois accès.

## Méthode d'implémentation

Le plan d'implémentation déléguera les tâches à des sous-agents, en routant
par tier de modèle (règle du workspace) : Haiku pour les scans/inventaires,
Sonnet pour l'implémentation cadrée (fetchers, scoring, site), Opus pour le
debug complexe et la modélisation, l'orchestration restant au modèle
principal. Les tâches indépendantes (un fetcher par source, pipeline vs web)
sont parallélisables entre sous-agents.

## Évolutions prévues (architecture prête, pas construites)

- Volet 2 : super-résolution ERA5 → champ côtier haute résolution (onglet
  "défis IA" supplémentaire sur le même site, même pipeline batch).
- Stations supplémentaires (ajout = une entrée de config + un modèle entraîné).
- Modèles plus ambitieux (deep learning) quand le scoreboard prouve la valeur.
