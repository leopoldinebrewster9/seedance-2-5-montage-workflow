import argparse,json,pathlib,subprocess,tempfile
try: import yaml
except ImportError: yaml=None
def load(path):
 if yaml is None: raise RuntimeError("pip install pyyaml")
 data=yaml.safe_load(pathlib.Path(path).read_text()); shots=data.get("shots",[]) if isinstance(data,dict) else []
 if not shots: raise ValueError("shots is empty")
 ids=set()
 for i,s in enumerate(shots):
  if not isinstance(s,dict) or not s.get("prompt"): raise ValueError("each shot needs prompt")
  s["id"]=s.get("id",f"shot-{i+1}")
  if s["id"] in ids: raise ValueError("duplicate shot id")
  ids.add(s["id"])
 return data
def concat(clips,out,music=None):
 with tempfile.NamedTemporaryFile("w",suffix=".ffconcat",delete=False) as f:
  f.write("ffconcat version 1.0\n")
  for clip in clips: f.write("file "+json.dumps(str(pathlib.Path(clip).resolve()))+"\n")
  manifest=f.name
 cmd=["ffmpeg","-y","-f","concat","-safe","0","-i",manifest]
 if music: cmd += ["-i",music,"-map","0:v:0","-map","1:a:0","-shortest"]
 cmd += ["-c:v","libx264","-pix_fmt","yuv420p",out]
 subprocess.run(cmd,check=True);pathlib.Path(manifest).unlink(missing_ok=True)
def main():
 ap=argparse.ArgumentParser();ap.add_argument("plan");ap.add_argument("--clips",nargs="*");ap.add_argument("--output",default="montage.mp4");a=ap.parse_args();load(a.plan)
 if a.clips: concat(a.clips,a.output)
if __name__=="__main__":main()
