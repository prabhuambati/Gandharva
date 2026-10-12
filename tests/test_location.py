import unittest
from collector.location import extract_public_location
class LocationTests(unittest.TestCase):
    def test_explicit(self):
        self.assertEqual(extract_public_location('Singer\nBased in Telangana','https://instagram.com/a/')['location_state'],'Telangana')
    def test_no_city_inference(self):
        for text in ['Hyd/Blr','Vijayawada, Hyderabad, Nagpur','From Kerala','Performing in Goa','Tour: Maharashtra']:
            self.assertEqual(extract_public_location(text,'x')['location_state'],'')
    def test_ambiguous(self):
        self.assertEqual(extract_public_location('Based in Telangana / Karnataka','x')['location_state'],'')
