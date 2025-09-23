# Sidecat - Sigrok Decode Automated Testing Framework

## Overview
Sidecat (SIgrok DECode Automated Testing) is a Python-based framework designed for use in [sigrok](https://sigrok.org/), [libsigrokdecode](https://sigrok.org/wiki/Libsigrokdecode), and sigrok decoders automated testing (CI/CD) pipelines. It runs a battery of test vectors, compares results against a reference database, and returns a non-zero exit code to signal failure. The framework supports concurrent test execution, JSON-based test vector definitions, and detailed error reporting.

## Features
- **Reference Comparison**: Compare test outputs against a reference database for verification.
- **Regeneration Mode**: Generate reference ground truth data (7zipped decoder outputs and metadata).
- **Test Vector Management**: Load and validate test vectors from JSON files.
- **Concurrent Execution**: Run multiple tests concurrently using Python's `concurrent.futures` and `subprocess`.
- **Customizable Options**: Support for quiet mode, debug output, progress tracking, and configurable paths for `sigrok-cli` and `7z`.
- **Error Handling**: Detailed exit codes for various failure scenarios (e.g., test failures, command-line errors, internal errors).
- **Windows Compatibility**: Includes warnings and workaround for Windows-specific Python command-line issues.

## Requirements
- Python 3.6+
- [sigrok-cli](https://sigrok.org/wiki/Sigrok-cli)
- [7-Zip](https://www.7-zip.org/)
- Optional: `jsonschema` for test vector validation (commented out in code)

## Usage
Run the script with Python, specifying test vectors and options as needed. Ensure `sigrok-cli` and `7z` are installed and accessible at the specified paths (or update paths via command-line arguments).

### Command-Line Arguments
- `-l, --load_tests <test_vectors.json more_vectors.json>`: Specify JSON files containing test vectors (default: `sidecat.json`).
- `-t, --test <decoder1:sample1:test1[:test2...]>`: Run specific tests in the format `decoder:sample:test`. Use `-t all` to run all tests or `-t` to list available tests.
- `-r, --regen`: Regenerate reference data (creates `reference.json` and `reference.7z`).
- `-p, --progress <none|5|10|20|25|33>`: Display progress updates in percentage steps (default: `10`).
- `-s, --sigrok_path <path>`: Path to `sigrok-cli` executable (default: `C:/Program Files/sigrok/sigrok-cli`).
- `-z, --sevenzip_path <path>`: Path to `7z` executable (default: `C:/Program Files/7-Zip`).
- `-c, --concurrency <number>`: Number of concurrent jobs (default: `4`).
- `-q, --quiet`: Suppress console output, only return exit code.
- `-d, --debug`: Enable debug output (use `-d -d` for more verbosity).
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
  output:
  ```
  usage: sidecat [-h] [-q] [-v] [-l [test_vectors.json [other_vectors.json ...]]]
               (-t [sample1:test1[:test2...] [sample2:testx...] [sample1:test3...] ...] | -r)
               [-p {none,5,10,20,25,33}] [-to TIMEOUT] [-s SIGROK_PATH] [-z SEVENZIP_PATH] [-c CONCURRENCY] [-d]
  error: Loaded ['sidecat.json'] test vectors, available decoder:sample:test combinations:
   mfm:fdd_fm:all
   mfm:fdd_mfm:all
   mfm:hdd_mfm:all
   mfm:hdd_mfm_sector:default:all:nopfxnosn:fields:reports:crc:headcrce:headpolycrce:datacrce:datapolycrce
  '-t' requires at least one argument - A list of tests to run. Format is either '-t all' or '-t sample1:test1:test2 sample2:testx sample1:test3' etc.
  Use '-t' to get a list of available sample:test combinations.
  Use '-t sample' to get detailed descriptions of all available tests for that particular sample.
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
        "size": int,
        "crc": hex,
        "blake2b": string,
        "sha256": string
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
- **Timeout Limits**: The timeout functionality (`--timeout`) is currently non-functional. I dont know how to implement it properly :|

## Contributing
Contributions are welcome! Please submit issues or pull requests to the [GitHub repository](https://github.com/raszpl/sidecat).

## License
GPL-3
