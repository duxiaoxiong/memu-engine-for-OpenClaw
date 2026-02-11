import subprocess
import json
import os
import sys


def verify_search():
    script_path = os.path.join(os.path.dirname(__file__), "search.py")
    query = "test query"

    # Simulate tool call
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
    )

    try:
        result = subprocess.run(
            [sys.executable, script_path, query, "--max-results", "5"],
            capture_output=True,
            text=True,
            check=False,  # We'll handle errors manually to see output
            env=env,
        )

        if result.returncode != 0:
            # Check if it's a valid JSON error response
            try:
                output = json.loads(result.stdout)
                if "error" in output and "results" in output:
                    print("Received valid JSON error response from search.py")
                    return
            except:
                pass
            print(f"search.py failed with return code {result.returncode}")
            print(f"Stdout: {result.stdout}")
            print(f"Stderr: {result.stderr}")
            sys.exit(1)

        output = json.loads(result.stdout)

        print("Search output parsed successfully.")

        assert "results" in output, "Output JSON must have 'results' key"
        results = output["results"]
        assert isinstance(results, list), "'results' must be a list"
        assert len(results) <= 5, f"Results count {len(results)} exceeds max-results 5"

        for item in results:
            assert "path" in item, "Result item must have 'path'"
            assert "snippet" in item, "Result item must have 'snippet'"
            assert "score" in item, "Result item must have 'score'"
            assert "source" in item, "Result item must have 'source'"

            # Check snippet length budget (SNIPPET_MAX = 700)
            assert len(item["snippet"]) <= 700, (
                f"Snippet length {len(item['snippet'])} exceeds max 700"
            )

        print(f"Verified {len(results)} search results.")

    except subprocess.CalledProcessError as e:
        print(f"Error calling search.py: {e}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON output: {e}")
        print(f"Raw output: {result.stdout}")
        sys.exit(1)
    except AssertionError as e:
        print(f"Assertion failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    verify_search()
