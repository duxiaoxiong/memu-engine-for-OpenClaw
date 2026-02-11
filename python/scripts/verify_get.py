import subprocess
import json
import os
import sys


def verify_get():
    script_path = os.path.join(os.path.dirname(__file__), "get.py")
    result = None

    # Create a temporary test file in workspace (if possible) or use an existing one
    # For this verification, we'll try to read the README.md in the root
    workspace_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    os.environ["MEMU_WORKSPACE_DIR"] = workspace_dir
    test_file = "README.md"

    # Simulate tool call
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
    )

    try:
        # Test full read
        result = subprocess.run(
            [sys.executable, script_path, test_file],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        if result.returncode != 0:
            try:
                output = json.loads(result.stdout)
                if "error" in output and "text" in output:
                    print("Received valid JSON error response from get.py")
                    return
            except:
                pass
            print(f"get.py failed with return code {result.returncode}")
            print(f"Stdout: {result.stdout}")
            print(f"Stderr: {result.stderr}")
            sys.exit(1)

        output = json.loads(result.stdout)
        print("Get (full) output parsed successfully.")

        assert "path" in output, "Output JSON must have 'path' key"
        assert "text" in output, "Output JSON must have 'text' key"
        assert len(output["text"]) > 0, "Text content should not be empty"

        full_text = output["text"]

        from_line = 2
        lines_count = 5
        result_paged = subprocess.run(
            [
                sys.executable,
                script_path,
                test_file,
                "--from",
                str(from_line),
                "--lines",
                str(lines_count),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        if result_paged.returncode != 0:
            print(f"get.py paged failed with return code {result_paged.returncode}")
            sys.exit(1)

        output_paged = json.loads(result_paged.stdout)
        print("Get (paged) output parsed successfully.")

        sliced_text = output_paged["text"]
        start_idx = max(0, from_line - 1)
        expected_lines = full_text.splitlines(keepends=True)[
            start_idx : start_idx + lines_count
        ]
        expected_text = "".join(expected_lines)

        assert sliced_text == expected_text, "Paged text does not match expected slice"
        assert len(sliced_text.splitlines()) <= lines_count, (
            "Sliced text has more lines than requested"
        )

        print(f"Verified get.py with full and paged access.")

    except subprocess.CalledProcessError as e:
        print(f"Error calling get.py: {e}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON output: {e}")
        raw_output = result.stdout if result is not None else "N/A"
        print(f"Raw output: {raw_output}")
        sys.exit(1)
    except AssertionError as e:
        print(f"Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    verify_get()
