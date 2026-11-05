# Les fonctions de `preparation_parametres.py`

Référence des fonctions du module `gen_admissions/preparation_parametres.py`,
par groupe. Le schéma commun : requête par année → fonction « effectifs » →
`additionner` → fonction « depuis_effectifs » (avec `seuil`, `part_min`,
`contexte` explicites) → `verifier_table` → `to_csv`.

## 1. Réglages et classes

- **`REGLAGES`** : valeurs par défaut. Champs : `seuil_effectif` (100),
  `part_min_service_habituel` (0,10), `bornes_age` (17, 64, 74),
  `bornes_age_duree` (59,), `bornes_tranches_h` (1, 2, 4, 6, 12, 24),
  `colonnes_site`, `colonnes_saison`, `mois_debut_saison` (9), et les listes de
  colonnes des tables (`colonnes_venues`, `blocs_passage`,
  `colonnes_contexte_passage`, `situations_avec_sejour`,
  `situations_sans_sejour`, `parts_soins_critiques`). Propriétés dérivées :
  `classes_age`, `classes_age_duree`, `tranches_h`.
- **`configurer(**valeurs)`** : modifie des champs de `REGLAGES` pour la
  session ; refuse un nom inconnu.
- **`classe_age(age)`**, **`classe_age_duree(age)`** : libellé de classe d'un âge.
- **`tranche_heures(duree_h)`** : tranche horaire d'une série de durées en heures.
- **`saison(dates)`** : saison épidémiologique (`2023-2024`, de septembre à août).
- **`saisons_completes(annees)`** : saisons entièrement couvertes par des années civiles.
- **`avec_site(colonnes, df=None, contexte=None)`** : `colonnes` précédées des
  colonnes de site : `contexte` s'il est donné (`[]` pour aucune), sinon
  `REGLAGES.colonnes_site` limité aux colonnes présentes dans `df`
  (avertissement unique si absente). À utiliser pour vos propres `groupby`.
- **`avec_saison(colonnes)`** : idem avec `REGLAGES.colonnes_saison`.

## 2. Socle : normalisation, rang, repli

- **`normaliser(df, colonnes_bloc, colonne_effectif="n", seuil=None, contexte=None)`** :
  effectifs → parts par bloc, règle des catégories rares (masse des lignes
  sous le seuil répartie uniformément entre elles). Renvoie bloc, catégories,
  `part`, `observe`.
- **`attribuer_rang(t, part_min=None, masque=None)`** : rang 1 (part ≥ part_min),
  2 (observée, sous part_min), 3 (complétée). `masque` : lignes concernées.
- **`table_proportions(effectifs, colonnes_bloc, colonnes_categorie, replis=(), seuil=None, contexte=None)`** :
  normalisation au niveau fin plus les niveaux de repli (sous-ensembles de
  `colonnes_bloc`, colonnes absentes laissées vides). Pour `sorties`,
  `niveaux`, `durees_ghm`, `venues`, `gravite`.
- **`additionner(effectifs_par_annee, colonnes)`** : concatène et somme `n`
  par `colonnes` (+ site présent).
- **`verifier_table(t, colonnes_bloc)`** : erreur si un bloc ne somme pas à 1.

## 3. Structure

- **`services_vers_yaml(df, chemin=None)`** : tableau de services → bloc YAML
  `services:` (`soins_critiques` en texte « rea,usc » → liste ; cases vides omises).

## 4. Venues aux urgences

- **`situation_venue(v)`** : `hospitalisation` / `mono_uhcd` si séjour chaîné ;
  sinon `9` → `deces`, `7` → `transfert`, `6` → `inconnu`, autre → `consultation`.
- **`gravite_ccmu(ccmu)`** : CCMU « 1 » à « 5 » (P → 1, D → 5), « » si inconnue.
- **`effectifs_venues(v)`** : exclut `inconnu` (part affichée) et séjours sans GHM ;
  gravité sur les venues sans séjour, racine sur les venues avec séjour ;
  comptage par (saison, mois, classe d'âge, situation, gravité, racine).
- **`venues_depuis_effectifs(effectifs, seuil=None, contexte=None)`** :
  `venues_urgences.csv`, bloc saison × mois × classe d'âge, repli saison × classe d'âge.

## 5. Profils des urgences

- **`effectifs_profil(df, dimensions)`** : comptage par (dimension, valeur) pour
  un dictionnaire nom → série.
- **`effectifs_arrivees(v)`** : composantes `semaine` (jour × heure) et `mois`.
- **`effectifs_entrees_programmees(sejours)`** : entrées programmées par jour de la semaine (et par heure si les dates en ont).
- **`profil_depuis_effectifs(effectifs)`** : parts par dimension, sans seuil.
- **`sexe_mf(sexe)`** : « M » / « F » quels que soient les codes.
- **`effectifs_patients_urgences(v)`**, **`patients_urgences_depuis_effectifs(effectifs)`** :
  classe d'âge × sexe, parts sur l'ensemble.
- **`effectifs_gravite(v)`**, **`gravite_depuis_effectifs(effectifs, seuil=None, contexte=None)`** :
  hospitalisés, classe d'âge × situation × tranche de durée × CCMU, repli sans la tranche.

## 6. Parcours

- **`premier_service_depuis_effectifs(effectifs, seuil=None, part_min=None, contexte=None)`** :
  parts et rang par racine × classe d'âge, repli par racine.
- **`etapes_sejours(rum)`** : RUM (sans UHCD) → une ligne par étape, avec
  `service`, `precedent` (`AUCUN`), `suite` (`sortie`) ; RUM consécutifs de
  même spécialité fusionnés.
- **`effectifs_passage(etapes)`** : transitions aux niveaux `complet` et `service`.
- **`table_passage_depuis_effectifs(effectifs, seuil=None, part_min=None, contexte=None)`** :
  `table_passage.csv`.

## 7. Durées et soins critiques

- **`passages_specialite(rum)`** : une ligne par passage de spécialité avec
  `duree_j` (jours décimaux ; dates converties si besoin).
- **`durees_passage_depuis_passages(passages, seuil=None, contexte=None)`** :
  médiane, quartiles, moyenne par spécialité × mode d'entrée × classe d'âge,
  repli par spécialité.
- **`soins_critiques_depuis_effectifs(fin, ghm, racine, seuil=None, contexte=None)`** :
  parts par GHM × classe d'âge avec repli (GHM seul, puis racine) appliqué à la fabrication.

## 8. Tension du jour

- **`tension_journaliere(sejours, debut, fin, contexte=None)`** : série
  quotidienne (par site) des patients présents au relevé, dont venus des
  urgences ; la tension est ce nombre, en valeur absolue. `sejours` : `date_entree`,
  `date_sortie`, `urgences` (0/1). Présent le jour J : entré avant J, sorti J
  ou après. À conserver : sert aux bornes, au contrôle, et à classer chaque
  venue pour `durees_passage_urgences` et `supplement_rang2`.
- **`classes_tension_depuis_tension(tension, quantiles=(1/3, 2/3), contexte=None)`** :
  `classes_tension.csv`, bornes (terciles) par site, en nombre de patients.
- **`classe_tension(tension, bornes)`** : classe (basse, moyenne, haute) d'une
  série de tensions d'après les bornes d'un site.

## 9. Durées de passage aux urgences

- **`classer_tension(tension, bornes, contexte=None)`** : ajoute `classe_tension`
  à la série quotidienne, site par site.
- **`effectifs_durees_urgences(v, tension_classee, premier_service_racine=None, contexte=None)`** :
  venues → situation, gravité (sans séjour), tranche de durée, classe de tension
  du jour d'arrivée (jointure sur site et date) ; si `premier_service_racine`
  est fournie, ne garde pour `hospitalisation` que le rang 1 ; comptage par
  situation × gravité × classe d'âge × classe de tension × tranche.
- **`durees_urgences_depuis_effectifs(effectifs, seuil=None, contexte=None)`** :
  `durees_passage_urgences.csv`, replis sans la tension puis sans la gravité.

## 10. Supplément de rang 2

- **`effectifs_supplement_rang2(v, tension_classee, premier_service_racine, contexte=None)`** :
  hospitalisés de rang 2 : supplément = durée − médiane des rang 1 de la même
  case (site, classe d'âge, classe de tension), négatifs à zéro, en tranches ;
  comptage par classe de tension × tranche.
- **`supplement_depuis_effectifs(effectifs, seuil=None, contexte=None)`** :
  `supplement_rang2.csv`, bloc classe de tension, repli sur l'ensemble.
- `normaliser` accepte désormais un bloc vide (`[]`) : les parts somment à 1 sur l'ensemble.
