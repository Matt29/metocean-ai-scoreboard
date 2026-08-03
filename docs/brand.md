# Charte — Ocean Data Consulting

**Ne pas recopier la charte ici.** Elle a une source unique, vivante :

```
~/Documents/DEV/WEB/ODC_WEBSITE/DESIGN_SYSTEM/
```

C'est un **skill Claude packagé** (`SKILL.md`, nom `oceandata-design`,
user-invocable). Avant toute tâche web de ce projet (Tasks 11-13), l'invoquer
— ou à défaut lire `DESIGN_SYSTEM/readme.md` en entier, qui est le guide de
marque faisant autorité (contexte entreprise, voix française en *vous*,
fondations couleur/type/espacement, iconographie Lucide).

## Ce qu'il y a dedans

| Chemin | Contenu |
|---|---|
| `readme.md` | guide de marque complet — **à lire en premier** |
| `styles.css` + `tokens/` | entrée CSS globale et tous les tokens (couleurs, typo, espacement, rayons, ombres, mouvement) |
| `components/` | `Button`, `Input`, `Select`, `Checkbox`, `Switch`, `IconButton`, `Card`, `StatCard`, `Badge`, `Tag`, `Avatar`, `Tabs`, `Tooltip` (JSX) |
| `ui_kits/` | recréations d'UI — `website/` (nav, hero, stats, services…) et `searoute/` (shell, panneau, mapview) |
| `assets/` | logo lockup + monogramme, versions transparente et blanc détouré |
| `guidelines/` | cartes HTML : échelles de couleur, type, espacement, rayons, ombres, dégradés |

**Consommer les alias sémantiques** (`--brand-accent`, `--surface-card`,
`--text-strong`…), jamais les valeurs brutes.

Les artefacts `_ds_bundle.js` et `_ds_manifest.json` sont **générés** : ne
jamais les éditer à la main. Modifier un `.jsx` rend le bundle périmé — le
signaler plutôt que de le contourner.

## Précédent à suivre

Le site (`ODC_WEBSITE/site/`, Vite + React Router + GSAP) intègre déjà un outil
comme page à part entière : `site/src/pages/SearoutePage.jsx`, avec son kit
`ui_kits/searoute/`. **Le scoreboard suit ce modèle** — voir la décision
d'architecture dans le ledger SDD avant d'attaquer les tâches web.

## Application au scoreboard

- Les chiffres (MAE, gains, verdicts) sont des **données** → rôle `--role-data`,
  IBM Plex Mono, alignés à droite dans les tableaux.
- Verdicts : PASS → succès, FAIL → danger, PASS\* / faible → warning.
  **Jamais la couleur seule** — libellé *et* couleur. Sur un scoreboard public
  dont l'argument est l'honnêteté, un lecteur daltonien doit pouvoir lire quelle
  station échoue.
- Voix : français, *vous*, scientifique et quantifié — comme le reste du site.
  Framing « post-processing ML du modèle physique », jamais « remplace la
  physique ».
