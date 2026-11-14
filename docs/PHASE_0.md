# Phase 0 — Synthea FHIR R4 feasibility

## Setup

Run from the standalone repository root on macOS ARM64. The observed run used Temurin 17.0.21+9 and CPython 3.13.13, both ARM64. The machine's default Java and Python were left unchanged. The Python script uses only the standard library.

If those runtimes are missing, place them under the ignored work directory. The Python install command used was:

```sh
mkdir -p work/tools
UV_PYTHON_INSTALL_DIR="$PWD/work/tools/python" UV_CACHE_DIR="$PWD/work/uv-cache" uv python install --no-bin 3.13
```

The JDK was selected from the [Adoptium ARM64 macOS Java 17 API](https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=aarch64&image_type=jdk&os=mac&vendor=eclipse). The exact archive and SHA-256 used are below. Download and verify the archive before extraction.

```sh
curl -fLsS --retry 2 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/OpenJDK17U-jdk_aarch64_mac_hotspot_17.0.21_9.tar.gz' -o work/tools/temurin17.tar.gz
printf '%s\n' '78b08c7f5a0598242d5719c022e19479cb4a3aa45c37c19726a757a469ed1c15  work/tools/temurin17.tar.gz' | shasum -a 256 -c
mkdir -p work/tools/java17
tar -xzf work/tools/temurin17.tar.gz -C work/tools/java17
```

Download the official [Synthea 4.0.0 release asset](https://github.com/synthetichealth/synthea/releases/expanded_assets/v4.0.0). The release page's published checksum matched the downloaded JAR.

```sh
curl -fLsS --retry 2 'https://github.com/synthetichealth/synthea/releases/download/v4.0.0/synthea-with-dependencies.jar' -o work/tools/synthea.jar
printf '%s\n' 'ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1  work/tools/synthea.jar' | shasum -a 256 -c
```

## Reproduce the experiment

The script reads [run.json](../config/synthea/run.json) and [phase0.properties](../config/synthea/phase0.properties). The configuration requests five patients in Massachusetts, population seed 202701, clinician seed 202702, reference and end date 2025-01-01, UTC, and one generation thread. It enables FHIR R4 Bundle JSON with bulk, transaction, and US Core modes disabled.

```sh
py=$(find "$PWD/work/tools/python" -path '*/bin/python3.13' -print -quit)
java=$(find "$PWD/work/tools/java17" -path '*/Contents/Home/bin/java' -print -quit)
"$py" -c 'import platform; print(platform.python_version(), platform.machine())'
"$java" -version
file "$java"
"$py" scripts/phase0.py --java "$java" --jar work/tools/synthea.jar --output data/generated/phase0/run-01
"$py" -m unittest discover -s tests -v
```

Use a new output directory for each invocation. The script verifies the JAR checksum before launch and prints both fully expanded Java commands. With the paths above, the Java invocation has this form, once for `baseline` and once for `repeat`:

```sh
run=baseline  # repeat for the second invocation
"$java" -Duser.timezone=UTC -jar "$PWD/work/tools/synthea.jar" \
  -c "$PWD/config/synthea/phase0.properties" -p 5 -s 202701 -cs 202702 \
  -r 20250101 -e 20250101 \
  --exporter.baseDirectory="$PWD/data/generated/phase0/run-01/$run" Massachusetts
```

Both runs retain their generator logs. The script stores the summary and mutation evidence in `data/generated/phase0/run-01/report.json`. All generated records are synthetic and stay in ignored directories.

## Observed results

| Check | Baseline | Independent repeat |
| --- | ---: | ---: |
| Distinct Patients | 5 | 5 |
| Bundle files | 7 | 7 |
| Resource entries | 3,099 | 3,099 |
| Resource types | 19 | 19 |

Both generator logs reported the expected population, seeds, location, and reference time, with five living records. The patient ID and birth-date projections matched. The resource totals include organization and practitioner exports.

The selected baseline Bundle yielded zero findings. Removing `/entry/0/resource/birthDate` in a copy produced exactly one `PATIENT_BIRTH_DATE_MISSING` finding for the selected synthetic patient. Parsed equality against an expected one-field removal verified the mutation. The original Bundle SHA-256 before and after was `e6d1a60ba487bdd96c271b57204a9e4d9c0b42fe31642e4a23913cffdf511f57`.

All seven local unit tests passed. They cover baseline and mutated input, missing/null/blank dates, incorrect types, malformed input, ambiguous targets, source preservation, no overwrite, and missing or duplicate exports.

## Limits

The experiment checks the Bundle structure it reads and applies a project completeness rule. [FHIR R4 Patient.birthDate](https://www.hl7.org/fhir/R4/patient.html) has cardinality 0..1, and no full structure, profile, terminology, or reference validation was performed. The repeated-run comparison covers patient IDs and birth dates. It does not establish byte-for-byte or cross-platform reproducibility.

The GitHub Actions workflow is present. A GitHub Issue, linked pull request, and hosted CI result remain pending until remote setup and push are authorized.
