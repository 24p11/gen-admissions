"""Simulateur v2 : lit les 22 fichiers de paramètres (parametres/DICTIONNAIRE.md).

Modules, dans l'ordre où ils s'empilent :
- ``parametres`` : charger, vérifier et préparer les tables (aucune notion de temps) ;
- ``tirages`` : fabriquer un patient des urgences ou une entrée programmée (étapes A, B, C) ;
- ``lits`` : les lits des services et la demande d'un lit (règles D) ;
- ``moteur`` : les processus SimPy ;
- ``export`` : les séjours et mouvements fictifs ;
- ``controle`` : les indicateurs comparés aux cibles.
"""
