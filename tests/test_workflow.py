import pathlib,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]))
from montage.workflow import load
class T(unittest.TestCase):
 def test_load(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/"x.yml";p.write_text("shots:\n - {id: a, prompt: hello}\n")
   try:self.assertEqual(load(p)["shots"][0]["id"],"a")
   except RuntimeError:self.skipTest("optional yaml")
if __name__=="__main__":unittest.main()
