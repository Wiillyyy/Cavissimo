"""Tests des premières règles métier, avec la bibliothèque standard."""

import unittest

from models import Bouteille, Etagere, Vin


class TestEtagere(unittest.TestCase):
    def setUp(self):
        self.vin = Vin("Domaine des amis", "Cuvée du partage", "rouge", 2022, "Savoie")
        self.etagere = Etagere("Mes rouges", 3)

    def test_depassement_sans_modification_du_stock(self):
        for _ in range(3):
            self.etagere.ajouter_bouteille(Bouteille(self.vin, 12.50))
        bouteille_en_trop = Bouteille(self.vin, 12.50)
        with self.assertRaises(ValueError):
            self.etagere.ajouter_bouteille(bouteille_en_trop)
        self.assertEqual(self.etagere.occupation(), 3)
        self.assertIsNone(bouteille_en_trop.etagere)

    def test_deux_exemplaires_partagent_la_meme_reference(self):
        premiere = Bouteille(self.vin, 12.50)
        seconde = Bouteille(self.vin, 14)
        self.etagere.ajouter_bouteille(premiere)
        self.etagere.ajouter_bouteille(seconde)
        self.assertIsNot(premiere, seconde)
        self.assertIs(premiere.vin, seconde.vin)
        self.assertEqual(self.etagere.occupation(), 2)

    def test_meme_bouteille_ne_peut_pas_etre_rangee_deux_fois(self):
        bouteille = Bouteille(self.vin, 12.50)
        self.etagere.ajouter_bouteille(bouteille)
        autre_etagere = Etagere("Mes blancs", 5)
        for destination in (self.etagere, autre_etagere):
            with self.assertRaises(ValueError):
                destination.ajouter_bouteille(bouteille)
        self.assertEqual(self.etagere.occupation(), 1)
        self.assertEqual(autre_etagere.occupation(), 0)

    def test_reduction_de_capacite_refusee_si_occupee(self):
        for _ in range(2):
            self.etagere.ajouter_bouteille(Bouteille(self.vin, 12.50))
        with self.assertRaises(ValueError):
            self.etagere.changer_capacite(1)
        self.assertEqual(self.etagere.capacite_max, 3)
        self.etagere.changer_capacite(2)
        self.assertEqual(self.etagere.capacite_max, 2)

    def test_capacites_invalides(self):
        for capacite in (0, -1, 2.5, True, "3"):
            with self.subTest(capacite=capacite):
                with self.assertRaises(ValueError):
                    Etagere("Mes rouges", capacite)


if __name__ == "__main__":
    unittest.main()
