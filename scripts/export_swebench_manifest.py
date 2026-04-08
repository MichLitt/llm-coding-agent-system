from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coder_agent.eval.benchmarks.swebench.manifest_export import main


if __name__ == "__main__":
    main()
