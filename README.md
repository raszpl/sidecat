# Sidecat - Sigrok Decode Automated Testing Framework

## Overview
Sidecat (SIgrok DECode Automated Testing) is a Python-based framework designed for automated testing of [sigrok](https://sigrok.org/), [libsigrokdecode](https://sigrok.org/wiki/Libsigrokdecode), and sigrok decoders within CI/CD pipeline. It executes test vectors, compares outputs against a reference database, and returns non-zero exit codes to signal failures. The framework supports concurrent test execution, JSON-based test vector definitions, and detailed error reporting.

## Features
- **Reference Comparison**: Validates test outputs against a reference database.
- **Regeneration Mode**: Generates reference ground truth data (7zipped decoder outputs and metadata).
- **Test Vector Management**: Loads and validates test vectors from JSON files.
- **Concurrent Execution**: Runs multiple tests concurrently using Python's `concurrent.futures`.
- **Customizable Options**: Supports quiet mode, debug output, progress tracking, and configurable paths for `sigrok-cli` and `7z`.
- **Error Handling**: Provides detailed exit codes for various failure scenarios (e.g., test failures, command-line errors, internal errors).
- **Windows Compatibility**: Includes warning and workaround for Windows-specific Python command-line issues.
- **JSON Schema Validation**: Optionally validates test vector JSON files using the `jsonschema` library.

## Requirements
- Python 3.6+
- [sigrok-cli](https://sigrok.org/wiki/Sigrok-cli)
- [7-Zip](https://www.7-zip.org/)
- Optional: `jsonschema` for test vector validation (enabled with `--jsonschema`)

## Usage
Run the script with Python, specifying test vectors and options as needed. Ensure `sigrok-cli` and `7z` are installed and accessible.

### Command-Line Arguments
- `-l, --load_tests <test_vectors.json> [more_vectors.json ...]`: Specify JSON files containing test vectors (default: `sidecat.json`).
- `-t, --test <decoder1:sample1:test1[:test2...] [decoder2:sample2:testx...]>`: Run specific tests in the format `decoder:sample:test`. Use `-t all` to run all tests, `-t` to list available tests, `-t decoder` to list samples for a decoder, or `-t decoder:sample` to list tests for a sample.
- `-r, --regen`: Regenerate reference data (creates `reference.json` and `reference.7z`).
- `-p, --progress <none|5|10|20|25|33>`: Display progress updates in percentage steps (default: `10`). Disabled by `--quiet`.
- `-s, --sigrok_path <path>`: Path to `sigrok-cli` executable (default: `C:/Program Files/sigrok/sigrok-cli` on Windows).
- `-z, --sevenzip_path <path>`: Path to `7z` executable (default: `C:/Program Files/7-Zip` on Windows).
- `-c, --concurrency <number>`: Number of concurrent jobs (default: `4`).
- `-q, --quiet`: Suppress console output, only return exit code.
- `-d, --debug`: Enable debug output. Use `-d -d` for more verbosity. Disables `--progress` and ignores `--quiet`.
- `--jsonschema`: Enable JSON schema validation for test vectors (requires `jsonschema` library).
- `-to, --timeout <seconds>`: Set timeout for test execution (default: `600`). Note: Currently non-functional.
- `-v, --version`: Show version.

### Example Commands
- Run all tests:
  ```bash
  python sidecat.py -t all
  ```
- Run specific tests:
  ```bash
  python sidecat.py -t decoder1:sample1:test1 decoder2:sample2:testx
  ```
- Regenerate reference data:
  ```bash
  python sidecat.py -r
  ```
- Run all tests with custom test vectors:
  ```bash
  python sidecat.py -l custom_tests.json more_tests.json -t all
  ```
- Run all tests with no output, only exit codes:
  ```bash
  python sidecat.py -t all -q
  ```
- Run all tests with debug output:
  ```bash
  python sidecat.py -d -t all
  ```
- List available tests:
  ```bash
  python sidecat.py -t
  ```
  Example output:
  ```
  error: Loaded ['sidecat.json'] test vectors, available decoder:sample:test combinations:
   mfm:fdd_fm:all
   mfm:fdd_mfm:all
   mfm:hdd_mfm:all
   mfm:hdd_mfm_sector:default:all:nopfxnosn:fields:reports:crc:headcrce:headpolycrce:datacrce:datapolycrce
  ```
- List samples for mfm decoder:
  ```bash
  python sidecat.py -t mfm
  ```
- List tests for mfm:hdd_mfm_sector:
  ```bash
  python sidecat.py -t mfm:hdd_mfm_sector
  ```

### Test Vector JSON Format
Test vectors are defined in JSON files (e.g., `sidecat.json`) with the following structure:
```json
{
  "decoder_name": {
    "sample_name": {
      "path": "optional/sample/specific/path",
      "test_name": {
        "options": "decoder_options",
        "annotate": "annotation_flags",
        "desc": "Test description (optional)",
        "size": 12345,
        "crc": "0x1a2b3c4d",
        "blake2b": "128-character-hex-string",
        "sha256": "64-character-hex-string"
      },
    },
  },
}
```

## Exit Codes
- `0`: All tests passed successfully.
- `1`: Some tests failed.
- `2`: Command-line usage error.
- `3`: Test execution interrupted by user (Ctrl+C).
- `4`: Internal error during test execution.
- `5`: `sigrok-cli` error.

## Notes
- **Windows Users**: When running via file association (e.g., `sidecat.py`), command-line arguments may not pass correctly. Use `python sidecat.py` instead, or modify the Windows registry to include `%*` in `HKEY_CLASSES_ROOT\Applications\py.exe\shell\open\command`.
- **Timeout Limits**: The `--timeout` option is currently non-functional due to implementation challenges aka I dont know how to make it work :|
- **JSON Schema Validation**: Enable with `--jsonschema` to validate test vector JSON files. Requires the `jsonschema` library.
- **File Paths**: Ensure `sigrok-cli` and `7z` executables are accessible. Use `--sigrok_path` and `--sevenzip_path` if not in PATH.
- **Sample location**: Sample files (e.g., `sample.sr`) must be in PATH or specified by the `path` parameter inside test vector JSON.

## Contributing
Contributions are welcome! Please submit issues or pull requests to the [GitHub repository](https://github.com/raszpl/sidecat).

## License
GPL v3 (GNU General Public License version 3.0)
