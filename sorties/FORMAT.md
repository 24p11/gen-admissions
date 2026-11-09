# Format des sorties du simulateur

Le simulateur écrit quatre tables qui imitent les tables administratives réelles —
mêmes noms de colonnes (la nomenclature harmonisée du DIM), mêmes codes — de sorte
qu'un traitement écrit pour les vraies tables tourne sur les tables fictives sans
modification. À ces colonnes s'ajoutent des colonnes **`sim_`**, la « vérité du
simulateur », qui n'existent pas dans le réel et servent à comprendre et à contrôler
(situation, service prévu, rang, attente…). On peut les ignorer ou les retirer d'un
`drop(columns=...)`.

Conventions communes :

- **une colonne `type`** dans chaque table, valant `SIM` (dans les tables réelles du
  DIM : `REEL`), pour pouvoir empiler fictif et réel sans ambiguïté ;
- **identifiants fictifs mais persistants** : `nohop` et `finess` sont ceux de
  `structure.yaml` (fictifs) ; `nas`, `no_visite` sont des numéros séquentiels ;
  `ipp` identifie la personne, qui garde le même numéro quand elle revient (la
  `probabilite_retour` de la structure), pour les analyses de réadmission ;
- **dates et heures** : toutes les colonnes de date portent la date **et l'heure**
  (`2026-03-14 17:42`) ; les vraies tables PMSI ont souvent le jour seul pour
  `d8eeue`/`d8soue` — il suffit de tronquer, l'inverse n'est pas possible ;
- **niveau de structure** : le simulateur connaît les services (une spécialité =
  une UMA) ; les mouvements sont donc au niveau de la spécialité, pas des unités
  d'hébergement plus fines qui existent dans le réel, et il n'y a pas de codes
  d'UMA (`uma_ej`) — ces listes sont une particularité du réel ;
- **écriture** : un fichier parquet et un CSV par table (`rpu`, `rsa`, `rum`,
  `mouvements`), plus `tension.parquet` (la série quotidienne de la tension
  simulée) et `resume.yaml` (paramètres, graine, période, comptages) pour la
  traçabilité. Les jours de chauffe et la marge de fin de la simulation sont
  retirés : seules les venues et les séjours entièrement compris dans la période
  utile sont écrits.

## `rpu` — une ligne par venue aux urgences

| colonne | contenu |
| --- | --- |
| `type` | `SIM` |
| `finess` | finess fictif du site (structure) |
| `nohop` | code du site (structure) |
| `no_visite` | numéro de la venue |
| `entree`, `sortie` | arrivée aux urgences, sortie des urgences (vers le domicile, un lit, un transfert) |
| `age`, `sexe` | âge en années ; sexe `1` homme, `2` femme |
| `gravite` | CCMU `1` à `5`, vide si inconnue |
| `mode_entree` | `8` (domicile) |
| `mode_sortie` | `6` mutation (hospitalisé, UHCD comprise), `7` transfert, `8` domicile, `9` décès |
| `nas` | numéro du séjour qui suit, vide sinon |
| `sim_situation` | consultation, mono_uhcd, hospitalisation, transfert, deces |
| `sim_classe_tension` | classe de tension du jour d'arrivée |
| `sim_service_prevu`, `sim_rang` | service demandé en premier et son rang (hospitalisés) |
| `sim_attente_lit_h` | temps entre la demande d'un lit et son obtention |
| `sim_uhcd` | `oui` si un lit d'UHCD a été occupé pendant le passage |

## `rsa` — une ligne par séjour

| colonne | contenu |
| --- | --- |
| `type` | `SIM` |
| `nohop`, `nas`, `ipp` | site, numéro de séjour, personne |
| `no_visite` | la venue aux urgences d'origine, vide pour un séjour programmé |
| `agean`, `sexe` | âge en années, sexe `1`/`2` |
| `ghm`, `gpcompx` | GHM (racine + niveau), niveau de sévérité |
| `d8eeue`, `d8soue` | entrée et sortie du séjour (date-heure) ; l'entrée d'un séjour venu des urgences est la fin du passage aux urgences — sauf passage par l'UHCD : le séjour commence alors à l'entrée en UHCD (son premier RUM), pendant la venue, et la venue se termine à la sortie de l'UHCD, comme dans le réel où l'UHCD fait partie de la structure des urgences |
| `duree` | nuitées (différence de dates calendaires) |
| `echpmsi` | mode d'entrée du séjour : `8` domicile |
| `prov` | provenance : `5` si venu des urgences, vide sinon |
| `cdpu` | `5` si venu des urgences, vide sinon (comme `prov`, au niveau du séjour) |
| `schpmsi`, `dest` | mode de sortie et destination : domicile `8`/vide, EHPAD `8`/`7`, SSR `7`/`2`, transfert `7`/`1`, décès `9`/vide |
| `nb_rum` | nombre de RUM, UHCD comprise |
| `sim_origine` | `urgences` ou `programme` |
| `sim_mono_uhcd` | `oui` pour un séjour limité à l'UHCD |
| `sim_soins_critiques` | `rea`, `usi`, `usc` prévus par le tirage, séparés par des virgules |

## `rum` — une ligne par passage dans un service

| colonne | contenu |
| --- | --- |
| `type` | `SIM` |
| `nohop`, `nas`, `norum` | site, séjour, numéro d'ordre du RUM |
| `noseqrum` | numéro du RUM principal du séjour (le plus long hors UHCD), répété sur chaque ligne |
| `spe_uma`, `lib_spe_uma` | code et libellé de la spécialité (= le service) |
| `typaut` | type d'autorisation de l'unité (`07A` pour l'UHCD, codes de la structure pour les soins critiques) |
| `cdurm` | code d'unité fictif (= le service) |
| `d8eeue`, `d8soue` | entrée et sortie du RUM (date-heure) |
| `mdeeue` | mode d'entrée du RUM : premier RUM `8` (domicile) ; RUM suivants `6` (mutation) |
| `cdpu` | `5` sur le premier RUM d'un séjour venu des urgences, vide sur les suivants (mutation) et sur les séjours programmés |
| `mdsoue` | mode de sortie du RUM : dernier RUM = `schpmsi` du séjour ; RUM intermédiaires `6` (mutation) |
| `agean`, `ghm` | repris du séjour |
| `sim_service_prevu`, `sim_rang` | service demandé et rang du service obtenu |
| `sim_heberge` | `oui` si le service est différent de la spécialité prévue (hébergement) |
| `sim_attente_h`, `sim_supplement_h` | attente d'un lit avant ce RUM ; supplément de rang 2 appliqué |

## `mouvements` — une ligne par mouvement administratif

Un mouvement par lit occupé : passage par l'UHCD, chaque service, et changement de lit
dans un même service s'il y en a. Il y a donc au moins autant de mouvements que de RUM.

| colonne | contenu |
| --- | --- |
| `type` | `SIM` |
| `nohop`, `nas`, `ipp` | site, séjour, personne |
| `nomvt`, `nbmvt` | numéro du mouvement, nombre de mouvements du séjour |
| `spe_uma` | la spécialité (= le service) ; pas d'`uma_ej` : les listes d'UMA sont une particularité du réel que la simulation ne reproduit pas |
| `d8eeue`, `d8soue` | entrée et sortie du mouvement (date-heure) |
| `prov` | `5` sur le premier mouvement d'un séjour venu des urgences, vide sinon |
| `lit` | numéro de lit fictif dans le service |
| `sim_type` | `entree`, `mutation`, `changement_lit` (les codes `ids_type_mouvement` du DIM ne sont pas repris) |

## Ce que les sorties ne contiennent pas

Pas de diagnostics ni d'actes (le GHM est tiré directement), pas d'identité, pas
d'adresse, pas de dates de naissance (seulement l'âge), pas d'unités plus fines que la
spécialité. Les colonnes `sim_` sont la seule trace de ce que le simulateur « savait »
et que le réel ne dit jamais : le service prévu (exprimé, comme le service obtenu, avec
les codes `spe_uma`), le rang, l'attente.
