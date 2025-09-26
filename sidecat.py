import concurrent.futures, subprocess, threading
import os, sys, argparse, json
import zlib, hashlib
from enum import Enum

lock = threading.RLock()
class globals:
	counter = 0
	progress = 0
	size = 0
	quiet = False
	debug = False

class ExitCode(Enum):
	success		= 0	# All tests passed successfully
	test_fail	= 1	# Some tests failed
	commandline	= 2	# Command line usage error
	user_ctrl_c	= 3	# Test execution was interrupted by the user
	internal	= 4	# Internal error during test execution
	sigrok_fail	= 5	# sigrok-cli error

# JSON schema for validating test vectors
schema = {
	"$schema": "http://json-schema.org/draft-07/schema#",
	"type": "object",
	"patternProperties": {
		"^[a-zA-Z0-9_\\-]+$": {
			"type": "object",
			"description": "Decoder name",
			"patternProperties": {
				"^[a-zA-Z0-9_\\-]+$": {
					"type": "object",
					"description": "Sample name",
					"properties": {
						"path": {
							"type": "string",
							"description": "Sample-specific path (optional)"
						}
					},
					"patternProperties": {
						"^(?!path$)[a-zA-Z0-9_\\-]+$": {
							"type": "object",
							"description": "Test name",
							"properties": {
								"options": {
									"type": "string",
									"description": "Decoder options (optional)"
								},
								"annotate": {
									"type": "string",
									"description": "Annotation flags (optional)"
								},
								"desc": {
									"type": "string",
									"description": "Test description (optional)"
								},
								"size": {
									"type": "integer",
									"minimum": 0,
									"description": "Size of the test data"
								},
								"crc": {
									"type": "string",
									"pattern": "^[0-9a-fA-F]{8}$",
									"description": "CRC checksum in hexadecimal"
								},
								"blake2b": {
									"type": "string",
									"pattern": "^[0-9a-fA-F]{128}$",
									"description": "Blake2b hash"
								},
								"sha256": {
									"type": "string",
									"pattern": "^[0-9a-fA-F]{64}$",
									"description": "SHA256 hash"
								}
							},
							"required": ["size", "crc", "blake2b", "sha256"],
							"additionalProperties": False
						}
					},
					"additionalProperties": False
				}
			},
			"additionalProperties": False
		}
	},
	"additionalProperties": False
}
schema_reference_pattern = schema["patternProperties"]["^[a-zA-Z0-9_\\-]+$"]["patternProperties"]["^[a-zA-Z0-9_\\-]+$"]["patternProperties"]["^(?!path$)[a-zA-Z0-9_\\-]+$"]
schema_reference_required = {"required": ["size", "crc", "blake2b", "sha256"]}

def json_validate(json_data, json_name, reference_required=False):
	try:
		from jsonschema import validate, ValidationError
		global schema

		if reference_required:
			schema_reference_pattern.update(schema_reference_required)
		else:
			if "required" in schema_reference_pattern:
				del schema_reference_pattern["required"]

		try:
			validate(instance=json_data, schema=schema)
		except ValidationError as e:
			parser.error(f"JSON file '{json_name}' validation Error: {e.message}\n"
				+ f"Path to error: {list(e.path)}\n"
				+ f"Bad instance: {e.instance}", ExitCode.internal.value)

	except ImportError:
		print_("Optional 'jsonschema' module could not be imported, skipping JSON validation.")

def json_load(json_file, reference_required=False):
	try:
		with open(json_file, 'r') as f:
			data = json.load(f)
	except FileNotFoundError:
		parser.error(f"Error: JSON file {json_file} not found", ExitCode.internal.value)
	except json.JSONDecodeError as e:
		parser.error(f"Error: Invalid JSON format in {json_file}: {e}", ExitCode.internal.value)
	except Exception as e:
		parser.error(f"JSON: An unexpected error occurred loading {json_file}: {e}", ExitCode.internal.value)

	json_validate(data, json_file, reference_required)

	return data

def json_save(json_file, data):
	try:
		with open(json_file, "w") as f:
			json.dump(data, f, indent='\t')
	except IOError as e:
		parser.error(f"Error: Could not write to file {json_file}. Check permissions or path. ({e})", ExitCode.internal.value)
	except Exception as e:
		parser.error(f"JSON: An unexpected error occurred: {e}", ExitCode.internal.value)

def check_path(file, file_path, mode=os.R_OK):
	test_path = os.path.join(file_path, file)
	if os.path.isfile(test_path):
		if os.access(test_path, mode):
			return test_path, 0
		else:
			return test_path, 1
	else:
		return test_path, 2

# ----------------------------------------------------------------------------
# special -q --quiet handling
def print_(*args):
	if not globals.quiet:
		print(" ".join(map(str, args)))

def print_d(*args):
	if globals.debug:
		print(" ".join(map(str, args)))

class QuietArgumentParser(argparse.ArgumentParser):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.add_argument("-q", "--quiet", action='store_true', help="No console output, only Exit Code.")

	def parse_args(self, args=None, namespace=None):
		args = args if args is not None else sys.argv[1:]
		# args into arg_all set helps with all comparisons against flag pairs
		arg_all = set(args)

		# print help when no arguments
		if not arg_all:
			self.print_help()

		# manually catch quiet and debug, need those early. Truthiness is all we need.
		globals.quiet = args.count('-q') + args.count('--quiet')
		globals.debug = args.count('-d') + args.count('--debug')

		arg_l = list(arg_all.intersection(set(['-l', '--load_tests'])))
		# Find load_tests action and run it manually, this is the only way to force
		# CustomLAction process its 'default'. We do it so help screen can display
		# loaded defaults
		if not arg_l:
			for action in self._actions:
				if action.dest == 'load_tests':
					action(self, None, action.default)
					break
		# also guard against '-l' being after -t or -r. we need it first
		else:
			arg_t_r = list(arg_all.intersection(set(['-t', '--test', '-r', '--reference'])))
			if arg_t_r:
				position_l = len(args) - 1 - list(reversed(args)).index(arg_l[0])
				position = args.index(arg_t_r[0])
				if position_l > position:
					self.error(f"'-l/--load_tests' needs to be in front of '{arg_t_r[0]}'")

		parsed_args = super().parse_args(args, namespace)
		return parsed_args

	def error(self, message, code=ExitCode.commandline.value):
		if not globals.quiet:
			if code == ExitCode.commandline.value:
				self.print_usage()
			self.exit(code, f"error: {message}\n")
		sys.exit(code)

	def print_help(self):
		if not globals.quiet:
			super().print_help()
		sys.exit(ExitCode.commandline.value)

# ----------------------------------------------------------------------------
class CustomLAction(argparse.Action):
	def __call__(self, parser, namespace, json_file, option_string=None):
		global test_vectors, tests_list_all

		# detect if we are being run from QuietArgumentParser aka
		# running with no '-l' and trying to load self.default
		if not namespace and json_file == self.default:
			print_d(f"Checking if default {self.default} exists.")
			_, file_path_result = check_path(self.default, '')
			if file_path_result:
				parser.error(f"{self.default} doesnt exists, no tests will be available.", ExitCode.internal.value)

		print_d(f"Trying to load {json_file}")
		test_vectors = json_load(json_file)
		tests_list_all = [
			(decoder, sample, test)
			for decoder in test_vectors
			for sample in test_vectors[decoder]
			for test in test_vectors[decoder][sample]
			if test != 'path'
		]
		print_d(f"Loaded {json_file} containing {len(tests_list_all)} tests for {len(test_vectors)} decoder(s). Available decoder:sample:test combinations:\n "
			+ '\n '.join([
				f"{decoder}:{sample}:{':'.join(key for key in tests.keys() if key != 'path')}"
				for decoder in test_vectors
				for sample in test_vectors[decoder]
				for tests in [test_vectors[decoder][sample]]
			])
		)
		if globals.debug > 1:
			print_d('test_vectors', json.dumps(test_vectors, indent='\t'))

		for decoder in test_vectors:
			for sample in test_vectors[decoder]:
				if sample == 'path':
					continue
				file_path, file_path_result = check_path(sample + '.sr', test_vectors[decoder][sample].get('path', ''))
				match file_path_result:
					case 0:
						print_d(f"Sample '{file_path}' exists and is accessible.")
					case 1:
						parser.error(f"Sample '{file_path}' exists but is not accessible.", ExitCode.internal.value)
					case 2:
						parser.error(f"Sample '{file_path}' does not exist.", ExitCode.internal.value)

		if namespace:
			setattr(namespace, self.dest, json_file)

class CustomTAction(argparse.Action):
	def __init__(self, option_strings, dest, nargs=None, **kwargs):
		self.raw_args = sys.argv[1:]
		super().__init__(option_strings, dest, nargs=nargs, **kwargs)

	def __call__(self, parser, namespace, values, option_string=None):
		global tests_selected
		tests_selected = []

		if len(test_vectors) == 0:
			parser.error(f"Default {parser.get_default('load_tests')} not loaded or empty, no decoder:sample:test(s) available.")

		if values == ['all']:
			tests_selected = tests_list_all
		# empty -t
		elif not values:
			parser.error(
				f"Loaded {getattr(namespace, 'load_tests')} containing following decoder:sample:test combinations:\n "
				+ '\n '.join([
					f"{decoder}:{sample}:{':'.join(key for key in tests.keys() if key != 'path')}"
					for decoder in test_vectors
					for sample in test_vectors[decoder]
					for tests in [test_vectors[decoder][sample]]
				])
			)
		# one or more bad -t arguments
		else:
			for value in values:
				# "decoder1:sample1:test1:test2" -> (["decoder1", "sample1", "test1"], ["decoder1", "sample1", "test2"])

				# extract decoder:sample:tests, ugly but works. FIXME?
				parts = list(map(str.strip, value.strip(':').split(':')))
				if len(parts) == 0:
					decoder, sample, tests = None, None, []
				elif len(parts) == 1:
					decoder, sample, tests = parts[0], None, []
				elif len(parts) == 2:
					decoder, sample, tests = parts[0], parts[1], []
				else:
					decoder, sample, *tests = parts

				# Validate decoder name
				if decoder not in test_vectors:
					available_decoders = '\n '.join(test_vectors.keys())
					parser.error(f"Invalid decoder name '{decoder}' in '{option_string} {value}'. Available decoders:\n {available_decoders}")

				# Validate sample name
				available_samples = '\n '.join([s for s in test_vectors[decoder] if s != 'path'])
				if not sample:
					parser.error(f"'{option_string} {decoder}' requires sample name. Available samples for '{decoder}':\n {available_samples}")

				if sample == 'path':
					parser.error(f"'path' is an illegal sample name. Available samples for '{decoder}':\n {available_samples}")

				if sample not in test_vectors[decoder]:
					parser.error(f"Invalid sample name '{sample}' for decoder '{decoder}' in '{option_string} {value}'. Available samples for '{decoder}':\n {available_samples}")

				max_key_len = len(max((k for k in test_vectors[decoder][sample].keys() if k != 'path'), key=len, default=''))
				# sample valid, but no tests given
				if len(tests) == 0:
					# list comprehension filter to handle stupid optional "desc" field, use longest sample name to left align/pad spaces (<) to nicely line up dashes
					if max_key_len:
						parser.error(
							f"'{option_string} {decoder}:{sample}' requires at least one test name. Available tests:\n "
							+ '\n '.join([
								f"{key:<{max_key_len}} - {value['desc']}"
								if 'desc' in value else key
								for key, value in test_vectors[decoder][sample].items()
								if key != 'path'
							])
						)
					else:
						parser.error(f"No tests available for {decoder}:{sample}' decoder:sample combination.")

				# Validate test names
				for test in tests:
					if test not in test_vectors[decoder][sample]:
						# same list comprehension filter as above
						parser.error(
							f"Invalid test '{test}' for '{decoder}:{sample}' in '{option_string} {value}'. Available tests:\n "
							+ '\n '.join([
								f"{key:<{max_key_len}} - {value['desc']}"
								if 'desc' in value else key
								for key, value in test_vectors[decoder][sample].items()
								if key != 'path'
							])
						)
					tests_selected.append([decoder, sample, test])

		setattr(namespace, self.dest, values)
# ----------------------------------------------------------------------------
def sigrok_cli(decoder, sample, test):
	try:
		test_vector = test_vectors[decoder][sample][test]
		option = test_vector['options'] if 'options' in test_vector else ''
		annotate = test_vector['annotate'] if 'annotate' in test_vector else ''
		sample_dir = test_vectors[decoder][sample].get('path', '')
		sample_path = os.path.join(sample_dir, f"{sample}.sr")

		print_d(f"{args.sigrok_path} -D -i {sample_path} -P {decoder}:{option} -A {decoder}={annotate}")

		# Run sigrok-cli, pump output into a pipe
		proc_sig = subprocess.Popen([
			args.sigrok_path,
			"-D",			# dont scan for hardware probes
			"-i",			# load our test sample
			f"{sample_path}",
			"-P",			# set decoder options
			f"{decoder}:{option}",
			"-A",			# set decoder annotations
			f"{decoder}={annotate}"],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			bufsize=0,		# Unbuffered
			text=False		# binary mode
		)

		hash_blake2b = hashlib.blake2b()
		hash_sha256 = hashlib.sha256()
		checksum = 0
		size = 0

		output_file = f"{decoder}-{sample}-{test}"
		f = None

		if args.sevenzip_path != 'none':
			output_path = output_file + '.7z'
			proc_7z = subprocess.Popen([
				args.sevenzip_path,
				"u",	# update archive if already present
				"-mx1",	# barely any compression, its temporary anyway
				f"-si{output_file}",	# piped input
				output_path],
				stdin=subprocess.PIPE,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=False
			)
			f = proc_7z.stdin
		else:
			output_dir = './'
			if args.reference:
				output_dir = './reference'
				os.makedirs(output_dir, exist_ok=True)
			output_path = os.path.join(output_dir, f"{output_file}")
			f = open(output_path, 'wb')

		progress_treshold = round(globals.size * globals.progress * 0.01)
		# pump that pipe Mario
		while True:
			data = proc_sig.stdout.read(65535)
			if not data:
				break
			f.write(data)

			size += len(data)
			checksum = zlib.crc32(data, checksum)
			hash_blake2b.update(data)
			hash_sha256.update(data)

			if globals.progress:
				with lock:
					globals.counter += len(data)
					if globals.counter >= progress_treshold:
						print(f"Progress: {globals.progress}% \r", end="")
						globals.progress = round((globals.counter / globals.size) * 100 / args.progress + 1) * args.progress
						progress_treshold = round(globals.size * globals.progress * 0.01)

		f.flush()
		f.close()

		proc_sig.wait()

		if proc_sig.returncode != 0:
			parser.error(f"sigrok-cli error (exit code:{proc_sig.returncode}) for {decoder}:{sample}:{test}: {proc_sig.stderr.read().decode()}", ExitCode.sigrok_fail.value)

		if args.sevenzip_path != 'none':
			# FIXME: this is wrong, timeout is set too late
			stdout2, stderr2 = proc_7z.communicate(timeout=args.timeout)
			if proc_7z.returncode != 0:
				# clean up potential leftover .7z.tmp garbage
				if os.path.exists(f"{output_path}.tmp"):
					os.remove(f"{output_path}.tmp")
				parser.error(f"7z (exit code:{proc_7z.returncode}) for job {decoder}:{sample}:{test} file {output_path}: {stderr2.decode()}", ExitCode.internal.value)

	except Exception as e:
		parser.error(f"sigrok_cli or Output processing for {decoder}:{sample}:{test} failed: {e}", ExitCode.internal.value)

	return {
		decoder: {
			sample: {
				test: {
					'size': size,
					'crc': f"{checksum:08x}",
					'blake2b': hash_blake2b.hexdigest(),
					'sha256': hash_sha256.hexdigest()
				}
			}
		}
	}

def reference_pack():
	if args.sevenzip_path == 'none':
		print_d("No 7z compression for reference packing.")
		return
	try:
		for decoder, sample, test in tests_list_all:
			output_file = f"{decoder}-{sample}-{test}"

			first_7z = f"{args.sevenzip_path} e {output_file}.7z {output_file} -so"
			second_7z = f'"{args.sevenzip_path}" u -mx9 -si{output_file} reference.7z'
			print_d(first_7z + " | " + second_7z)

			proc_7z1 = subprocess.Popen(
				first_7z,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=False,
			)
			proc_7z2 = subprocess.run(
				second_7z,
				stdin=proc_7z1.stdout,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=False,
			)

			stderr2 = proc_7z2.stderr
			_, stderr1 = proc_7z1.communicate(timeout=args.timeout)
			if proc_7z1.returncode != 0:
				parser.error(f"7z error (exit code:{proc_7z1.returncode}) while unpacking {output_file}.7z: {stderr1.decode()}", ExitCode.internal.value)

			if proc_7z2.returncode != 0:
				parser.error(f"7z error (exit code:{proc_7z2.returncode}) reference packing failed in job {output_file} file reference.7z: {stderr2.decode()}", ExitCode.internal.value)

			# clean up individual test 7zips
			if os.path.exists(f"{output_file}.7z"):
				os.remove(f"{output_file}.7z")

	except Exception as e:
		parser.error(f"7z reference packing failed: {e}", ExitCode.internal.value)

def compare_with_reference(output, reference):
	test_failures = []
	total_tests = 0

	if globals.debug > 1:
		print_d('reference', json.dumps(reference, indent='\t'))
		print_d('output', json.dumps(output, indent='\t'))

	for decoder in output:
		for sample in output[decoder]:
			for test in output[decoder][sample]:
				total_tests += 1
				output_file = f"{decoder}-{sample}-{test}"
				diff_instruction = ""
				if args.sevenzip_path != 'none':
					diff_instruction = (
						f"\n\tTo diff, extract files and compare:\n"
						f"\t  {args.sevenzip_path} e reference.7z {output_file} -o./reference\n"
						f"\t  {args.sevenzip_path} e {output_file}.7z {output_file} -o./test\n"
						f"\t  diff ./reference/{output_file} ./test/{output_file}"
					)
				else:
					diff_instruction = (
						f"\n\tTo diff, compare files directly:\n"
						f"\t  diff ./reference/{output_file} ./test/{output_file}"
					)

				if decoder not in reference:
					test_failures.append(f"Decoder '{decoder}' not found in reference")
					continue

				if sample not in reference[decoder]:
					test_failures.append(f"Sample '{sample}' not found in reference for decoder '{decoder}'")
					continue

				if test not in reference[decoder][sample]:
					test_failures.append(f"Test '{test}' not found in reference for '{decoder}:{sample}'")
					continue

				ref_data = reference[decoder][sample][test]
				gen_data = output[decoder][sample][test]

				if 'size' not in gen_data:
					test_failures.append(f"Test '{decoder}:{sample}:{test}' - No size data generated")
					continue

				if ref_data.get('size') != gen_data['size']:
					test_failures.append(f"Test '{decoder}:{sample}:{test}' - Size mismatch: expected {ref_data.get('size', 'N/A')}, got {gen_data['size']}{diff_instruction}")
					continue

				if 'crc' in ref_data and 'crc' in gen_data:
					if ref_data['crc'] != gen_data['crc']:
						test_failures.append(f"Test '{decoder}:{sample}:{test}' - CRC mismatch: expected {ref_data['crc']}, got {gen_data['crc']}{diff_instruction}")

				if 'blake2b' in ref_data and 'blake2b' in gen_data:
					if ref_data['blake2b'] != gen_data['blake2b']:
						test_failures.append(f"Test '{decoder}:{sample}:{test}' - Blake2b mismatch{diff_instruction}")

				if 'sha256' in ref_data and 'sha256' in gen_data:
					if ref_data['sha256'] != gen_data['sha256']:
						test_failures.append(f"Test '{decoder}:{sample}:{test}' - SHA256 mismatch{diff_instruction}")

	if test_failures:
		parser.error(f"\n{len(test_failures)} test(s) failed:\n"
			+ "\n".join(f" - {failure}"
				for failure in test_failures
			), ExitCode.test_fail.value)
	else:
		print_(f"\nAll {total_tests} tests passed verification against the loaded reference data")
		sys.exit(ExitCode.success.value)

def dict_merge_preserve_source_order(source, add_this):
	result = {}
	for key in source:
		if (
			key in add_this
			and isinstance(source[key], dict)
			and isinstance(add_this[key], dict)
		):
			result[key] = dict_merge_preserve_source_order(source[key], add_this[key])
		else:
			result[key] = add_this.get(key, source[key])
	for key in add_this:
		if key not in source:
			result[key] = add_this[key]
	return result

def main():
	global parser # we will use custom parser.error(message, exitcode) thru whole program
	global args, test_vectors, tests_list_all, tests_selected
	test_vectors = {}
	tests_list_all = []
	tests_selected = []
	output_files = {}

	epilog = "\n\nExit code meaning:" +\
		"\n 0: All tests passed successfully" +\
		"\n 1: Some tests failed" +\
		"\n 2: Command line usage error" +\
		"\n 3: Test execution was interrupted by the user" +\
		"\n 4: Internal error during test execution" +\
		"\n 5: sigrok-cli error"

	if os.path.basename(sys.executable).endswith(".exe"):
		epilog += "\n\nWindows Warning!\nPython command line parameter passing works only when directly invoked with python interpreter:\n\
 >python sidecat.py -whatever\n\
Using automagic .py Windows file association by executing command:\n\
 >sidecat.py -whatever\n\
will NOT pass any parameters. HKEY_CLASSES_ROOT\\Applications\\py.exe\\shell\\open\\command only passes .py file and nothing else. Stupid defaults can be changed by adding %* at the end of this registry key."

	parser = QuietArgumentParser(prog='sidecat', description="SIgrok DECode Automated Testing (%(prog)s) framework for sigrok, libsigrokdecode and sigrok decoders. Runs battery of test vectors, compares results against reference database, sets non-zero Exit Code on failure.", epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument('-v', '--version', action='version', version='%(prog)s v1.0')
	# needs to be here on top so help alt
	parser.add_argument("-l", "--load_tests", metavar='test_vectors.json', type=os.path.abspath, action=CustomLAction, default="sidecat.json", help="JSON file containing custom test vectors, default %(default)s")

	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument('-t', '--test', nargs='*', metavar='decoder1:sample1:test1[:test2...] [decoder2:sample2:testx...] [decoder1:sample1:test3...]', type=str, action=CustomTAction, help="A list of tests to run. Format is either '-t all' or '-t decoder1:sample1:test1:test2 decoder2:sample2:testx decoder1:sample1:test3' etc.\nUse '-t' to get a list of available decoder:sample:test combinations.\nUse '-t decoder' for a list of available samples for that decoder.\nUse '-t decoder:sample' to get detailed descriptions of all available tests for that combination.")
	group.add_argument("-r", "--reference", action='store_true', help="Generate reference ground truth (collection of 7zipped Decoder outputs or uncompressed files) using predefined test vector table.")

	parser.add_argument("-p", "--progress", choices=['none', '5', '10', '20', '25', '33'], default='10', help="Display progress updates in %% steps. --quiet flag disables it. Default %(default)s")
	# FIXME: timeouts are weird in concurrent python, cant make it work
	parser.add_argument("-to", "--timeout", type=int, default=600, help="Default %(default)s seconds. FIXME: doesnt work at the moment.")
	parser.add_argument("-s", "--sigrok_path", type=os.path.abspath, default="C:/Program Files/sigrok/sigrok-cli", help="Location of sigrok-cli executable, defaults to %(default)s")
	parser.add_argument("-z", "--sevenzip_path", type=str, default="C:/Program Files/7-Zip", help="Location of 7zip 7z executable, defaults to %(default)s. Use 'none' to force uncompressed files.")
	parser.add_argument("-c", "--concurrency", type=int, default=4, help="Number of concurrent jobs. Maximum is number of test cases, default %(default)s")
	parser.add_argument("-d", "--debug", action='store_true', help="Debug prints, use '-d -d' to debug even harder! Disables --progress, ignores --quiet.")
	args = parser.parse_args()

	globals.size = sum([
		test_vectors[decoder][sample][test].get('size', 0)
		for decoder in test_vectors
		for sample in test_vectors[decoder]
		for test in test_vectors[decoder][sample]
		if test != 'path'
	])

	if globals.debug:
		args.progress = 'none'

	if args.test:
		print_d("Building test_vectors_selected consisting of test_vectors we want to run.")
		test_vectors_selected = {
			decoder: {
				sample: {
					test: test_vectors[decoder][sample][test]
					for test in set(t[2] for t in tests_selected if t[0] == decoder and t[1] == sample)
				}
				for sample in set(t[1] for t in tests_selected if t[0] == decoder)
			}
			for decoder in set(t[0] for t in tests_selected)
		}
		print_d("Validating if all test_vectors_selected have references.")
		json_validate(test_vectors_selected, 'test_vectors_selected', reference_required=True)
		if not len(test_vectors):
			parser.error(f"Loaded test vectors do not contain reference data.", ExitCode.internal.value)

		globals.size = sum([
			test_vectors[decoder][sample][test].get('size', 0)
			for decoder, sample, test in tests_selected
		])

	if args.reference:
		print_d(f"Regenerating {len(tests_list_all)} tests for {len(test_vectors)} decoders.")
		tests_selected = tests_list_all

	if args.progress != 'none' and not args.quiet:
		args.progress = int(args.progress)
		# progress counter onyl possible if we have size
		if globals.size > 0:
			globals.progress = args.progress

	args.sigrok_path, sigrok_path_result = check_path('sigrok-cli' + ('.exe' if os.path.basename(sys.executable).endswith(".exe") else ''), args.sigrok_path, os.X_OK)
	match sigrok_path_result:
		case 0:
			print_d(f"sigrok-cli located at	{args.sigrok_path}")
		case 1:
			parser.error(f"The file '{args.sigrok_path}' exists but is not accessible.", ExitCode.internal.value)
		case 2:
			parser.error(f"The file '{args.sigrok_path}' does not exist.", ExitCode.internal.value)

	if args.sevenzip_path != 'none':
		args.sevenzip_path, sevenzip_path_result = check_path('7z' + ('.exe' if os.path.basename(sys.executable).endswith(".exe") else ''), args.sevenzip_path, os.X_OK)
		match sevenzip_path_result:
			case 0:
				print_d(f"7zip located at		{args.sevenzip_path}")
			case 1:
				parser.error(f"The file '{args.sevenzip_path}' exists but is not accessible.", ExitCode.internal.value)
			case 2:
				if args.sevenzip_path == parser.get_default('sevenzip_path'):
					print_(f"Warning: Default '{args.sevenzip_path}' file does not exist, switching to uncompressed output.")
					args.sevenzip_path = 'none'
				else:
					parser.error(f"The file '{args.sevenzip_path}' does not exist.", ExitCode.internal.value)

	print_(f"Starting {min(args.concurrency, len(tests_selected))} concurrent sigrok_cli instances. {len(tests_selected)} test cases to process.")

	with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
		futures = [
			executor.submit(sigrok_cli, decoder, sample, test)
			for decoder, sample, test in tests_selected
		]
		try:
			for future in concurrent.futures.as_completed(futures):
				result = future.result()
				if result:
					output_files = dict_merge_preserve_source_order(output_files, result)

		except KeyboardInterrupt:
			executor.shutdown()
			parser.error("\nCtrl-C pressed! Performing cleanup...", ExitCode.user_ctrl_c.value)
		except Exception as e:
			executor.shutdown()
			parser.error(f"Worker raised an error: {e}", ExitCode.internal.value)
		print_("")

	if args.reference:
		if globals.debug > 1:
			print_d('test_vectors', json.dumps(test_vectors, indent='\t'))
			print_d('output_files', json.dumps(output_files, indent='\t'))

		output_files = dict_merge_preserve_source_order(test_vectors, output_files)
		file_to_update = args.load_tests
		temp_file = file_to_update + '.tmp'
		json_save(temp_file, output_files)
		json_load(temp_file, reference_required=True)

		if not os.access(file_to_update, os.W_OK):
			parser.error(f"Cannot update '{file_to_update}': No write permission.", ExitCode.internal.value)
		try:
			os.remove(file_to_update)
		except OSError as e:
			parser.error(f"Failed to remove '{file_to_update}': {e}", ExitCode.internal.value)

		try:
			os.rename(temp_file, file_to_update)
		except OSError as e:
			parser.error(f"Failed to rename '{temp_file}' to '{file_to_update}': {e}", ExitCode.internal.value)

		reference_pack()
		print_(f"All sigrok_cli instances completed. {file_to_update} updated with metadata and {'reference.7z' if args.sevenzip_path != 'none' else f'{globals.counter / 1000**2:.2f}MB of uncompressed files in ./reference/'} generated successfully.")
		sys.exit(ExitCode.success.value)

	if args.test:
		json_save('test_report.json', output_files)
		compare_with_reference(output_files, test_vectors)

if __name__ == "__main__":
	main()
