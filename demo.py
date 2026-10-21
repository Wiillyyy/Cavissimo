"""Lancer avec : python demo.py"""

from models import Bouteille, Etagere, Vin


def main():
    vin = Vin("Domaine des amis", "Cuvée du partage", "rouge", 2022, "Savoie")
    etagere = Etagere("Mes rouges", 3)

    for numero in range(1, 5):
        # Un nouvel objet à chaque tour, mais toujours la même référence de vin.
        bouteille = Bouteille(vin, 12.50)
        try:
            etagere.ajouter_bouteille(bouteille)
            print(f"Bouteille {numero} ajoutée.")
        except ValueError as erreur:
            print(f"Bouteille {numero} refusée : {erreur}")

    print(f"Occupation : {etagere.occupation()}/{etagere.capacite_max}")
    print(f"Vin des bouteilles : {vin.nom_cuvee} ({vin.millesime})")


if __name__ == "__main__":
    main()
