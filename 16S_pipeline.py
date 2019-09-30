import luigi
import os
import sys
from subprocess import check_output, CalledProcessError
import logging

# Define custom logger
logger = logging.getLogger("custom logger")

# Path to configuration file to be used
luigi.configuration.add_config_path("configuration/luigi.cfg")

def run_cmd(cmd, step):
    try:
        output = check_output(cmd)
    except CalledProcessError as err:
        logger.error("In {step} following error occured\n{err}".format(
            step=step,
            err=err
            ))
        sys.exit(1)
    except Exception as err:
        logger.error("In {step} unknown error occured\n{err}".format(
            step=step,
            err=err
            ))
        raise err

    return output

class Out_Prefix(luigi.Config):
    prefix = luigi.Parameter()

class Output_Dirs(luigi.Config):
    # Define output paths
    out_dir = Out_Prefix().prefix
    denoise_dir = os.path.join(out_dir, "dada2")
    taxonomy_dir = os.path.join(out_dir, "taxonomy")
    export_dir = os.path.join(out_dir, "exported")

class Samples(luigi.Config):
    """
    Global variables that multiple steps may need access to.
    Includes...
        1. Manifest file (.txt) (maybe only accept .txt extension?)

    """
    manifest_file = luigi.Parameter()

class Import_Data(luigi.Task):
    # Options for qiime tools import
    sample_type = luigi.Parameter(
            default='SampleData[PairedEndSequencesWithQuality]')
    input_format = luigi.Parameter(default="PairedEndFastqManifestPhred33")

    def output(self):
        paired_end_demux = os.path.join(Output_Dirs().out_dir, "paired-end-demux.qza")

        return luigi.LocalTarget(paired_end_demux)

    def run(self):
        step = str(self)
        # Make output directory
        run_cmd(['mkdir',
                '-p',
                Output_Dirs().out_dir],
                self)

        inputPath = Samples().manifest_file

        # Make sure input file actually exists
        try:
            with open(inputPath, 'r') as fh:
                fh.readlines()
        except FileNotFoundError:
            logger.error("Input file for qiime tools import does not exist...")
            sys.exit(1)
        # in case of unexpected errors
        except Exception as err:
            logger.error(
            "In Import_Data() following error occured\n" + str(err))
            raise

        cmd = ["qiime",
                "tools",
                "import",
                "--type",
                self.sample_type,
                "--input-path",
                inputPath,
                "--output-path",
                self.output().path,
                "--input-format",
                self.input_format]

        output = run_cmd(cmd, self)

class Summarize(luigi.Task):

    def requires(self):
        return Import_Data()

    def output(self):
        summary_file = os.path.join(Output_Dirs().out_dir, "paired-end-demux.qzv")

        return luigi.LocalTarget(summary_file)

    def run(self):
        step = str(self)
        # Make output directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().out_dir],
                step)

        # Generate summary file
        cmd = ["qiime",
                "demux",
                "summarize",
                "--i-data",
                self.input().path,
                "--o-visualization",
                self.output().path]

        run_cmd(cmd, step)

class Denoise(luigi.Task):
    trim_left_f = luigi.Parameter(default="19")
    trunc_len_f = luigi.Parameter(default="250")
    trim_left_r = luigi.Parameter(default="20")
    trunc_len_r = luigi.Parameter(default="250")
    n_threads = luigi.Parameter(default="10")

    def requires(self):
        return Import_Data()

    def output(self):
        denoise_table = os.path.join(Output_Dirs().denoise_dir, "dada2-table.qza")
        rep_seqs = os.path.join(Output_Dirs().denoise_dir, "dada2-rep-seqs.qza")
        denoise_stats = os.path.join(Output_Dirs().denoise_dir, "stats-dada2.qza")
        dada2_log = os.path.join(Output_Dirs().denoise_dir, "dada2_log.txt")

        out = {
                "table": luigi.LocalTarget(denoise_table),
                "rep_seqs": luigi.LocalTarget(rep_seqs),
                "stats": luigi.LocalTarget(denoise_stats),
                "log": luigi.LocalTarget(dada2_log, format=luigi.format.Nop)
                }

        return out

    def run(self):
        step = str(self)
        # Make output directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().denoise_dir],
                step)

        # Run dada2
        cmd = ["qiime",
                "dada2",
                "denoise-paired",
                "--i-demultiplexed-seqs",
                self.input().path,
                "--p-trim-left-f",
                self.trim_left_f,
                "--p-trunc-len-f",
                self.trunc_len_f,
                "--p-trim-left-r",
                self.trim_left_r,
                "--p-trunc-len-r",
                self.trunc_len_r,
                "--p-n-threads",
                self.n_threads,
                "--o-table",
                self.output()["table"].path,
                "--o-representative-sequences",
                self.output()["rep_seqs"].path,
                "--o-denoising-stats",
                self.output()["stats"].path,
                "--verbose"]

        output = run_cmd(cmd, step)

        # Write a log file
        with self.output()["log"].open('wb') as fh:
            fh.write(output)

class Denoise_Tabulate(luigi.Task):

    def requires(self):
        return Denoise()

    def output(self):
        denoise_tabulated = os.path.join(Output_Dirs().denoise_dir, "stats-dada2.qzv")

        return luigi.LocalTarget(denoise_tabulated)

    def run(self):
        step = str(self)

        # Make output directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().denoise_dir],
                step)

        # Run qiime metadata tabulate
        cmd = ["qiime",
                "metadata",
                "tabulate",
                "--m-input-file",
                self.input()["stats"].path,
                "--o-visualization",
                self.output().path]

        run_cmd(cmd, step)

class Taxonomic_Classification(luigi.Task):
    classifier = luigi.Parameter()
    n_jobs = luigi.Parameter(default="10")

    def requires(self):
        return Denoise()

    def output(self):
        classified_taxonomy = os.path.join(Output_Dirs().taxonomy_dir, "taxonomy.qza")
        taxonomy_log = os.path.join(Output_Dirs().taxonomy_dir, "taxonomy_log.txt")

        output = {
                "taxonomy": luigi.LocalTarget(classified_taxonomy),
                "log": luigi.LocalTarget(taxonomy_log, format=luigi.format.Nop)
                }

        return output

    def run(self):
        step = str(self)

        # Make output directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().taxonomy_dir],
                step)

        # Run qiime classifier
        cmd = ["qiime",
                "feature-classifier",
                "classify-sklearn",
                "--i-classifier",
                self.classifier,
                "--i-reads",
                self.input()["rep_seqs"].path,
                "--o-classification",
                self.output()["taxonomy"].path,
                "--p-n-jobs",
                self.n_jobs,
                "--verbose"]

        output = run_cmd(cmd, step)

        # Log result
        with self.output()["log"].open('wb') as fh:
            fh.write(output)

class Taxonomy_Tabulate(luigi.Task):

    def requires(self):
        return Taxonomic_Classification()

    def output(self):
        tabulated = os.path.join(Output_Dirs().taxonomy_dir, "taxonomy.qzv")

        return luigi.LocalTarget(tabulated)

    def run(self):
        step = str(self)

        # Make output directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().taxonomy_dir],
                step)

        # Tabulate taxonomy classification result
        cmd = ["qiime",
                "metadata",
                "tabulate",
                "--m-input-file",
                self.input()["taxonomy"].path,
                "--o-visualization",
                self.output().path]

        run_cmd(cmd, step)

class Export_Feature_Table(luigi.Task):

    def requires(self):
        return Denoise()

    def output(self):
        biom = os.path.join(Output_Dirs().export_dir, "feature-table.biom")

        return luigi.LocalTarget(biom)

    def run(self):
        step = str(self)
        # Make directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().export_dir],
                step)

        # Export file
        cmd = ["qiime",
                "tools",
                "export",
                "--input-path",
                self.input()["table"].path,
                "--output-path",
                os.path.dirname(self.output().path)]

        run_cmd(cmd, step)

class Export_Taxonomy(luigi.Task):

    def requires(self):
        return Taxonomic_Classification()

    def output(self):
        tsv = os.path.join(Output_Dirs().export_dir, "taxonomy.tsv")

        return luigi.LocalTarget(tsv)

    def run(self):
        step = str(self)
        # Make directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().export_dir],
                step)

        # Export file
        cmd = ["qiime",
                "tools",
                "export",
                "--input-path",
                self.input()["taxonomy"].path,
                "--output-path",
                os.path.dirname(self.output().path)]

        run_cmd(cmd, step)

class Export_Representative_Seqs(luigi.Task):

    def requires(self):
        return Denoise()

    def output(self):
        fasta = os.path.join(Output_Dirs().export_dir, "dna-sequences.fasta")

        return luigi.LocalTarget(fasta)

    def run(self):
        step = str(self)
        # Make directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().export_dir],
                step)

        # Export file
        cmd = ["qiime",
                "tools",
                "export",
                "--input-path",
                self.input()["rep_seqs"].path,
                "--output-path",
                os.path.dirname(self.output().path)]

        run_cmd(cmd, step)

class Convert_Biom_to_TSV(luigi.Task):

    def requires(self):
        return Export_Feature_Table()

    def output(self):
        tsv = os.path.join(Output_Dirs().export_dir, "feature-table.tsv")

        return luigi.LocalTarget(tsv)

    def run(self):
        step = str(self)
        # Make output directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().export_dir],
                step)

        # Convert to TSV
        cmd = ["biom",
                "convert",
                "-i",
                self.input().path,
                "-o",
                self.output().path,
                "--to-tsv"]

        run_cmd(cmd, step)

class Generate_Combined_Feature_Table(luigi.Task):

    def requires(self):
        return {
                "Export_Taxonomy": Export_Taxonomy(),
                "Export_Representative_Seqs": Export_Representative_Seqs(),
                "Convert_Biom_to_TSV": Convert_Biom_to_TSV()
                }

    def output(self):
        combined_table = os.path.join(Output_Dirs().export_dir, "ASV_table_combined.tsv")
        log = os.path.join(Output_Dirs().export_dir, "ASV_table_combined.log")

        output = {
                "table": luigi.LocalTarget(combined_table),
                "log": luigi.LocalTarget(log, format=luigi.format.Nop)
                }

        return output

    def run(self):
        step = str(self)

        # Make output directory
        run_cmd(["mkdir",
                "-p",
                Output_Dirs().export_dir],
                step)

        # Run Jackson's script
        cmd = ["generate_combined_feature_table.py",
                "-f",
                self.input()["Convert_Biom_to_TSV"].path,
                "-s",
                self.input()["Export_Representative_Seqs"].path,
                "-t",
                self.input()["Export_Taxonomy"].path,
                "-o",
                self.output()["table"].path]

        logged = run_cmd(cmd, step)

        # Write a log file
        with self.output()["log"].open('w') as fh:
            fh.write(logged)


class Run_All(luigi.Task):
    def requires(self):
        return [
                Import_Data(),
                Summarize(),
                Denoise(),
                Denoise_Tabulate(),
                Taxonomic_Classification(),
                Taxonomy_Tabulate(),
                Export_Feature_Table(),
                Export_Taxonomy(),
                Export_Representative_Seqs(),
                Convert_Biom_to_TSV(),
                Generate_Combined_Feature_Table()
                ]

if __name__ == '__main__':
    luigi.run()
