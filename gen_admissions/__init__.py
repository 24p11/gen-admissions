"""gen-admissions : générateur de données administratives hospitalières fictives.

Le simulateur fait « vivre » un hôpital virtuel (simulation à événements
discrets avec SimPy) et produit des tables proches des formats réels :
passages aux urgences (RPU), séjours (RSS), unités médicales (RUM) et
mouvements, accompagnées d'une vérité de référence et d'un rapport de contrôle.
"""

__version__ = "0.1.0"
