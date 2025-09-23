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
	jsonschema = False

class ExitCode(Enum):
	success		= 0	# All tests passed successfully
	test_fail	= 1	# Some tests failed
	commandline	= 2	# Command line usage error
	user_ctrl_c	= 3	# Test execution was interrupted by the user
	internal	= 4	# Internal error during test execution
	sigrok_fail	= 5	# sigrok-cli error

#print(json.dumps(test_vectors, indent='\t'))

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
                  "pattern": "^0x[0-9a-fA-F]+$",
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

def dict_merge_preserve_source_order(source, add_this):
	# dont change order so it matches whatever load_json loaded
	result = {}
	for key in source:
		if key in add_this and isinstance(source[key], dict) and isinstance(add_this[key], dict):
			result[key] = dict_merge_preserve_source_order(source[key], add_this[key])
		else:
			result[key] = add_this.get(key, source[key])

	for key in add_this:
		if key not in source:
			result[key] = add_this[key]

	return result

def load_json(json_file):
	try:
		with open(json_file, 'r') as f:
			data = json.load(f)
	except FileNotFoundError:
		print_(f"Error: JSON file {json_file} not found")
		sys.exit(ExitCode.internal.value)
	except json.JSONDecodeError as e:
		print_(f"Error: Invalid JSON format in {json_file}: {e}")
		sys.exit(ExitCode.internal.value)
	except Exception as e:
		print_(f"JSON: An unexpected error occurred loading {json_file}: {e}")
		sys.exit(ExitCode.internal.value)

	if globals.jsonschema:
		from jsonschema import validate, ValidationError
		try:
			validate(instance=data, schema=schema)
		except ValidationError as e:
			print_(f"JSON file '{json_file}' validation Error: {e.message}")
			print_(f"Path to error: {list(e.path)}")
			print_(f"Bad instance: {e.instance}")
			sys.exit(ExitCode.internal.value)
	return data
		
def check_path(file, file_path, mode = os.R_OK):
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
	if not globals.quiet: print(" ".join(map(str, args)))

def print_d(*args):
	if globals.debug: print(" ".join(map(str, args)))

class QuietArgumentParser(argparse.ArgumentParser):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.add_argument("-q", "--quiet", action='store_true', help="No console output, only Exit Code.")

	def parse_args(self, args=None, namespace=None):
		args = args if args is not None else sys.argv[1:]
		# args into arg_all set helps with all comparisons against flag pairs
		arg_all = set(args)

		# print help when no arguments
		if not arg_all: self.print_help()

		# manually catch quiet and debug, need those early. Truthiness is all we need.
		globals.quiet = args.count('-q') + args.count('--quiet')
		globals.debug = args.count('-d') + args.count('--debug')
		globals.jsonschema = '--jsonschema' in args

		# Find load_tests action and run it manually, this is the only way to force
		# CustomLAction process its 'default'. We do it so help screen can display
		# loaded defaults
		arg_l = list(arg_all.intersection(set(['-l', '--load_tests'])))
		
		if not arg_l:
			for action in self._actions:
				if action.dest == 'load_tests':
					action(self, None, action.default)
					break
		else: # also guard against '-l' being after -t or -r. we need it first
			arg_t_r = list(arg_all.intersection(set(['-t', '--test', '-r', '--regen'])))
			if arg_t_r:
				position_l = len(args) - 1 - list(reversed(args)).index(arg_l[0])
				position = args.index(arg_t_r[0])
				if position_l > position:
					self.error(f"'-l/--load_tests' needs to be in front of '{arg_t_r[0]}'")

		parsed_args = super().parse_args(args, namespace)
		return parsed_args

	def error(self, message, code = ExitCode.commandline.value):
		if not globals.quiet:
			self.print_usage()
			self.exit(code, f"error: {message}\n")
		sys.exit(code)

	def print_help(self):
		if not globals.quiet:
			super().print_help()
		sys.exit(ExitCode.commandline.value)
# ----------------------------------------------------------------------------
class CustomLAction(argparse.Action):
	def __call__(self, parser, namespace, values, option_string=None):
		global loaded_tests, test_vectors
		test_vectors = {}

		if not values:
			parser.error(f"'{option_string}' requires at least one argument - {self.help % {'default': self.default}}")
		else:
			# detect if we are being run from QuietArgumentParser aka
			# running with no '-l' and trying to load self.default
			if not namespace and values == self.default:
				print_d(f"Trying to load default {self.default}, checking if it exists.")
				_, file_path_result = check_path(self.default, '')
				if file_path_result:
					print_d(f"{self.default} doesnt exists, no tests will be available.")
					return
				# self.default is a string, we need list
				values = [values]

			for value in values:
				tests = load_json(value)
				print_d(f"Loaded {value} tests")
				if globals.debug > 1: print_d(json.dumps(tests, indent='\t'))
				loaded_tests.append(value)
				
				test_everything = 1
				if test_everything:
					for decoder in tests:
						for sample in tests[decoder]:
							if sample == 'path':  # Handle path at decoder level
								continue
							file_path, file_path_result = check_path(sample+'.sr', tests[decoder][sample].get('path', ''))
							match file_path_result:
								case 0:
									print_d(f"The file '{file_path}' exists and is accessible.")
								case 1:
									parser.error(f"The file '{file_path}' exists but is not accessible.")
								case 2:
									parser.error(f"The file '{file_path}' does not exist.")

							# Clean up path if it exists
							if 'path' in tests[decoder][sample]:
								del tests[decoder][sample]['path']

				test_vectors.update(tests)

		if namespace: setattr(namespace, self.dest, test_vectors)

class CustomTAction(argparse.Action):
	def __init__(self, option_strings, dest, nargs=None, **kwargs):
		self.raw_args = sys.argv[1:]
		super().__init__(option_strings, dest, nargs=nargs, **kwargs)

	def __call__(self, parser, namespace, values, option_string=None):
		global test_vectors, test_list, loaded_tests
		test_list = []

		if len(test_vectors) == 0:
			parser.error(f"Default {parser.get_default('load_tests')} not loaded or empty, no decoder:sample:test(s) available.")

		if values == ['all']:
			test_list = [(decoder, sample, test) for decoder in test_vectors 
						for sample in test_vectors[decoder] 
						if sample != 'path' 
						for test in test_vectors[decoder][sample]]
		# empty -t or with one bad argument
		elif not values:
			parser.error(f"Loaded {loaded_tests} test vectors, available decoder:sample:test combinations:\n " +
				'\n '.join([f"{decoder}:{sample}:{':'.join(tests.keys())}" for decoder in test_vectors for sample in test_vectors[decoder] if sample != 'path' for tests in [test_vectors[decoder][sample]]]))
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
				available_decoders = '\n '.join(test_vectors.keys())
				if decoder not in test_vectors:
					parser.error(f"Invalid decoder name '{decoder}' in '{option_string} {value}'. Available decoders:\n {available_decoders}")

				# Validate sample name
				available_samples = '\n '.join([s for s in test_vectors[decoder] if s != 'path'])
				if not sample:
					parser.error(f"'{option_string} {decoder}' requires sample name. Available samples for '{decoder}':\n {available_samples}")

				if sample == 'path':
					parser.error(f"'path' is an illegal sample name. Available samples for '{decoder}':\n {available_samples}")
				
				if sample not in test_vectors[decoder]:
					parser.error(f"Invalid sample name '{sample}' for decoder '{decoder}' in '{option_string} {value}'. Available samples for '{decoder}':\n {available_samples}")

				# sample valid, but no tests given
				if len(tests) == 0:
					# sphagetti oneliner to handle stupid optional "desc" field, use longest sample name to left align/pad spaces (<) to nicely line up dashes
					parser.error(f"'{option_string} {decoder}:{sample}' requires at least one test name. Available tests:\n " +
						'\n '.join([f"{k:<{len(max(test_vectors[decoder][sample],key=len))}} - {v['desc']}" if 'desc' in v else k for k, v in test_vectors[decoder][sample].items()]))

				# Validate test names
				for test in tests:
					if test not in test_vectors[decoder][sample]:
						# same sphagetti oneliner as above
						parser.error(f"Invalid test '{test}' for '{decoder}:{sample}' in '{option_string} {value}'. Available tests:\n " +
							'\n '.join([f"{k:<{len(max(test_vectors[decoder][sample],key=len))}} - {v['desc']}" if 'desc' in v else k for k, v in test_vectors[decoder][sample].items()]))
					test_list.append([decoder, sample, test])

		setattr(namespace, self.dest, test_list)
# ----------------------------------------------------------------------------
def save_json(json_file, data):
	try:
		with open(json_file, "w") as f:
			json.dump(data, f, indent='\t')
	except IOError as e:
		print_(f"Error: Could not write to file {json_file}. Check permissions or path. ({e})")
		sys.exit(ExitCode.internal.value)
	except Exception as e:
		print_(f"JSON: An unexpected error occurred: {e}")
		sys.exit(ExitCode.internal.value)

def sigrok_cli(decoder, sample, test):
	try:
		test_vector = test_vectors[decoder][sample][test]
		option = test_vector['options'] if 'options' in test_vector else ''
		annotate = test_vector['annotate'] if 'annotate' in test_vector else ''
		output_file = f"{decoder}_{sample}_{test}"
		print_d(f"{args.sigrok_path} -D -i ../test/{sample}.sr -P {decoder}:{option} -A {decoder}={annotate}")

		# Run sigrok-cli, pump output into a pipe
		proc_sig = subprocess.Popen([
			args.sigrok_path,
			"-D",			# dont scan for hardware probes
			"-i",			# load our test sample
			f"../test/{sample}.sr",
			"-P",			# set decoder options
			f"{decoder}:{option}",
			"-A",			# set decoder annotations
			f"{decoder}={annotate}"],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			bufsize=0,		# Unbuffered
			text=False		# binary mode
		)

		proc_7z = subprocess.Popen([
			args.sevenzip_path,
			"u",			# update archive if already present
			"-mx1",
			f"-si{output_file}",	# piped input
			f"{output_file}.7z"],
			stdin=subprocess.PIPE,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=False
		)

		hash_blake2b = hashlib.blake2b()
		hash_sha256 = hashlib.sha256()
		checksum = 0
		size = 0

		# pump that pipe Mario
		while True:
			# show_progress flag used only to make sure we dont print inside
			# a lock, no idea if makes any sense considering python GIL
			show_progress = False
			data = proc_sig.stdout.read(65535)
			if not data:
				break
			proc_7z.stdin.write(data)

			size += len(data)
			checksum = zlib.crc32(data, checksum)
			hash_blake2b.update(data)
			hash_sha256.update(data)

			if globals.progress:
				with lock:
					globals.counter += len(data)
					if globals.counter > (globals.size * (globals.progress * 0.01)) or globals.counter == globals.size:
						globals.progress += args.progress
						show_progress = globals.progress
				if show_progress:
					print(f"Progress: {int((show_progress - args.progress))}% \r", end="")

		proc_7z.stdin.flush()
		proc_7z.stdin.close()

		# Wait for sigrok process to complete
		# FIXME: this is most likely wrong, timeout is set too late and you
		# only need communicate or wait, not both
		proc_sig.wait()
		stdout2, stderr2 = proc_7z.communicate(timeout=args.timeout)
		proc_7z.wait()
		_, stderr1 = proc_sig.communicate()
		
		if proc_sig.returncode != 0:
			print_(f"sigrok-cli error (exit code:{proc_sig.returncode}) for {decoder}:{sample}:{test}: {stderr1.decode()}")
			sys.exit(ExitCode.sigrok_fail.value)

		if proc_7z.returncode != 0:
			print_(f"7z error (exit code:{proc_7z.returncode}) for job {decoder}:{sample}:{test}: {stderr2.decode()}")
			# clean up potential leftover .7z.tmp garbage
			if os.path.exists(f"{output_file}.7z.tmp"):
				os.remove(f"{output_file}.7z.tmp")
			sys.exit(ExitCode.internal.value)

	except Exception as e:
		print_(f"sigrok_cli or 7z instance {decoder}:{sample}:{test} failed: {e}")
		sys.exit(ExitCode.internal.value)

	return {decoder: {sample: {test: {'size': size, 'crc': hex(checksum), 'blake2b': hash_blake2b.hexdigest(), 'sha256': hash_sha256.hexdigest()}}}}

def reference_pack(test_list):
	try:
		for decoder, sample, test in test_list:
			output_file = f"{decoder}_{sample}_{test}"

			first_7z = f"{args.sevenzip_path} e {output_file}.7z {output_file} -so"
			second_7z = f"{args.sevenzip_path} u -mx9 -si{output_file} reference.7z"
			print_d(first_7z + " | " + second_7z)
			
			subprocess.Popen(first_7z + " | " + second_7z,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=False,
				shell=True
			)

			_, stderr2 = proc_7z.communicate(timeout=args.timeout)
			proc_7z.wait()
			if proc_7z.returncode != 0:
				print_(f"7z error (exit code:{proc_7z.returncode}) reference packing failed for {output_file}: {stderr2.decode()}")
				# clean up potential leftover .tmp garbage
				if os.path.exists(f"{output_file}.7z.tmp"):
					os.remove(f"{output_file}.7z.tmp")
				sys.exit(ExitCode.internal.value)

			# clean up individual test 7zips
			if os.path.exists(f"{output_file}.7z"):
				os.remove(f"{output_file}.7z")

	except Exception as e:
		print_(f"7z reference packing failed: {e}")
		sys.exit(ExitCode.internal.value)

def compare_with_reference(output, reference):
	test_failures = []
	total_tests = 0
	
	if globals.debug > 1: print_d('reference', json.dumps(reference, indent='\t'))
	print_d('output', json.dumps(output, indent='\t'))
	
	for decoder in output:
		for sample in output[decoder]:
			for test in output[decoder][sample]:
				total_tests += 1
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
					test_failures.append(f"Test '{decoder}:{sample}:{test}' - Size mismatch: expected {ref_data.get('size', 'N/A')}, got {gen_data['size']}")
					continue

				if 'crc' in ref_data and 'crc' in gen_data:
					if ref_data['crc'] != gen_data['crc']:
						test_failures.append(f"Test '{decoder}:{sample}:{test}' - CRC mismatch: expected {ref_data['crc']}, got {gen_data['crc']}")

				if 'blake2b' in ref_data and 'blake2b' in gen_data:
					if ref_data['blake2b'] != gen_data['blake2b']:
						test_failures.append(f"Test '{decoder}:{sample}:{test}' - Blake2b mismatch")

				if 'sha256' in ref_data and 'sha256' in gen_data:
					if ref_data['sha256'] != gen_data['sha256']:
						test_failures.append(f"Test '{decoder}:{sample}:{test}' - SHA256 mismatch")

	if test_failures:
		print_(f"\n{len(test_failures)} test(s) failed:")
		for failure in test_failures:
			print_(f"  - {failure}")
		sys.exit(ExitCode.test_fail.value)
	else:
		print_(f"\nAll {total_tests} tests passed verification against reference.json")
		sys.exit(ExitCode.success.value)

def main():
	global args, loaded_tests, test_vectors, test_list
	loaded_tests = []
	test_vectors = {}
	output_files = {}
	test_list = []

	epilog = "\n\nExit code meaning:" +\
		"\n 0: All tests passed successfully" +\
		"\n 1: Some tests failed" +\
		"\n 2: Command line usage error" +\
		"\n 3: Test execution was interrupted by the user" +\
		"\n 4: Internal error during test execution" +\
		"\n 5: sigrok-cli error"

	if os.path.basename(sys.executable).endswith(".exe"):
		epilog += "\n\nWindows Warning!\nPython command line parameter passing works only when directly invoked with python interpreter:\n\
 >python ci.py -whatever\n\
Using automagic .py Windows file association by executing command:\n\
 >ci.py -whatever\n\
will NOT pass any parameters. HKEY_CLASSES_ROOT\\Applications\\py.exe\\shell\\open\\command only passes .py file and nothing else. Stupid defaults can be changed by adding %* at the end of this registry key."

	parser = QuietArgumentParser(prog='sidecat', description="SIgrok DECode Automated Testing (%(prog)s) framework for sigrok, libsigrokdecode and sigrok decoders. Runs battery of test vectors, compares results against reference database, sets non-zero Exit Code on failure.", epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument('-v', '--version', action='version', version='%(prog)s v1.0')
	# needs to be here on top
	parser.add_argument("-l", "--load_tests", nargs='*', metavar=('test_vectors.json', 'other_vectors.json'), type=str, action=CustomLAction, default="sidecat.json", help="A list of JSON files containing custom test vectors, default %(default)s")

	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument('-t', '--test', nargs='*', metavar='decoder1:sample1:test1[:test2...] [decoder2:sample2:testx...] [decoder1:sample1:test3...]', type=str, action=CustomTAction, help="A list of tests to run. Format is either '-t all' or '-t decoder1:sample1:test1:test2 decoder2:sample2:testx decoder1:sample1:test3' etc.\nUse '-t' to get a list of available decoder:sample:test combinations.\nUse '-t decoder' for a list of available samples for that decoder.\nUse '-t decoder:sample' to get detailed descriptions of all available tests for that particular combination.")
	group.add_argument("-r", "--regen", action='store_true', help="Generate reference ground truth (collection of 7zipped Decoder outputs) using predefined test vector table.")

	parser.add_argument("-p", "--progress", choices=['none', '5', '10', '20', '25', '33'], default='10', help="Display progress updates in %% steps. --quiet flag disables it. Default %(default)s")
	# FIXME: timeouts are weird in concurrent python, cant make it work
	parser.add_argument("-to", "--timeout", type=int, default=600, help="Default %(default)s seconds. FIXME: doesnt work at the moment.")
	parser.add_argument("-s", "--sigrok_path", type=os.path.abspath, default="C:/Program Files/sigrok/sigrok-cli", help="Location of sigrok-cli executable, defaults to %(default)s")
	parser.add_argument("-z", "--sevenzip_path", type=os.path.abspath, default="C:/Program Files/7-Zip", help="Location of 7zip 7z executable, defaults to %(default)s")
	parser.add_argument("-c", "--concurrency", type=int, default=4, help="Number of concurrent jobs. Maximum is number of test cases, default %(default)s")
	parser.add_argument("-d", "--debug", action='store_true', help="Debug prints, use '-d -d' to debug even harder! Disables --progress, ignores --quiet.")
	parser.add_argument("--jsonschema", action='store_true', help="Validate JSON files using optional jsonschema library.")
	args = parser.parse_args()

	# Updated size calculation for new structure
	globals.size = sum([
		test_vectors[decoder][sample][test].get('size', 0) 
		for decoder in test_vectors 
		for sample in test_vectors[decoder] 
		if sample != 'path' 
		for test in test_vectors[decoder][sample]
	])

	if globals.debug: args.progress = 'none'

	if args.test:
		reference_data = load_json('reference.json')
		if not len(reference_data):
			parser.error(f"reference.json doesnt contain reference database.", ExitCode.internal.value)

	if args.regen:
		# Updated test_list generation for new structure
		test_list = [(decoder, sample, test) for decoder in test_vectors 
					for sample in test_vectors[decoder] 
					if sample != 'path' 
					for test in test_vectors[decoder][sample]]
		print_d(f"Regenerating {len(test_list)} tests for {len(test_vectors)} decoders")

	if args.progress != 'none' and not args.quiet:
		args.progress = int(args.progress)
		globals.progress = args.progress

	args.sigrok_path, sigrok_path_result = check_path('sigrok-cli' + ('.exe' if os.path.basename(sys.executable).endswith(".exe") else ''), args.sigrok_path, os.X_OK)
	match sigrok_path_result:
		case 0:
			print_d(f"sigrok-cli located at	{args.sigrok_path}")
		case 1:
			parser.error(f"The file '{args.sigrok_path}' exists but is not accessible.", ExitCode.internal.value)
		case 2:
			parser.error(f"The file '{args.sigrok_path}' does not exist.", ExitCode.internal.value)

	args.sevenzip_path, sevenzip_path_result = check_path('7z' + ('.exe' if os.path.basename(sys.executable).endswith(".exe") else ''), args.sevenzip_path, os.X_OK)
	match sevenzip_path_result:
		case 0:
			print_d(f"7zip located at		{args.sevenzip_path}")
		case 1:
			parser.error(f"The file '{args.sevenzip_path}' exists but is not accessible.", ExitCode.internal.value)
		case 2:
			parser.error(f"The file '{args.sevenzip_path}' does not exist.", ExitCode.internal.value)

	print_(f"Starting {min(args.concurrency, len(test_list))} concurrent sigrok_cli instances. {len(test_list)} test cases to process.")

	with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
		futures = [executor.submit(sigrok_cli, decoder, sample, test) for decoder, sample, test in test_list]
		try:
			for future in concurrent.futures.as_completed(futures):
				result = future.result()
				if result:
					output_files = dict_merge_preserve_source_order(output_files, result)

		except KeyboardInterrupt:
			print_("\nCtrl-C pressed! Performing cleanup...")
			executor.shutdown()
			sys.exit(ExitCode.user_ctrl_c.value)
		except Exception as e:
			print_(f"Worker raised an error: {e}")
			executor.shutdown()
			sys.exit(ExitCode.internal.value)

	if args.regen:
		print_d('test_vectors',json.dumps(test_vectors, indent='\t'))
		print_d('output_files',json.dumps(output_files, indent='\t'))

		output_files = dict_merge_preserve_source_order(test_vectors, output_files)
		#merged_output = {}
		#for decoder in test_vectors:
		#	merged_output[decoder] = {}
		#	for sample in test_vectors[decoder]:
		#		if sample == 'path':
		#			merged_output[decoder][sample] = test_vectors[decoder][sample]
		#			continue
		#		merged_output[decoder][sample] = {}
		#		for test in test_vectors[decoder][sample]:
		#			if test in output_files.get(decoder, {}).get(sample, {}):
		#				merged_output[decoder][sample][test] = output_files[decoder][sample][test]
		#			else:
		#				# Copy from test_vectors if no generated data
		#				merged_output[decoder][sample][test] = test_vectors[decoder][sample][test]
		#
		#output_files = merged_output
		save_json('reference.json', output_files)
		save_json('regen', output_files)
		reference_pack(test_list)
		print_("All sigrok_cli instances completed. reference.7z and reference.json metadata generated successfully.")
		sys.exit(ExitCode.success.value)

	if args.test:
		save_json('test.json', output_files)
		compare_with_reference(output_files, reference_data)

if __name__ == "__main__":
	main()
