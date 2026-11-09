"""gen-admissions : générateur de données administratives hospitalières fictives.

Deux moitiés :

- ``gen_admissions.extraction`` : les fonctions qui transforment des effectifs
  agrégés (issus des vraies tables de l'établissement, ou d'un monde fictif) en
  fichiers de paramètres — proportions, profils, cibles (voir ``parametres/``) ;
- ``gen_admissions.simulateur`` : la simulation à événements discrets (SimPy) qui
  fait vivre l'hôpital décrit par ces paramètres et écrit des tables au format des
  tables administratives réelles — RPU, RSA, RUM, mouvements (voir ``sorties/FORMAT.md``).
"""

__version__ = "0.2.0"
