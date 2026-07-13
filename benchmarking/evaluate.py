#!/usr/bin/env python3
"""
Evaluate extraction results against ground truth data.

Usage:
    python benchmarking/evaluate.py --tag sonnet_test_02_2026
    python benchmarking/evaluate.py --tag opus_test_02_2026 --verbose
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import numpy as np
from sklearn.metrics import (
    accuracy_score,
    precision_recall_fscore_support,
    confusion_matrix,
    cohen_kappa_score,
)


@dataclass
class GroundTruth:
    """Single ground truth entry from CSV."""
    original_url: str
    replication_url: str
    result: str
    replication_es: str
    replication_es_type: str
    replication_n: str
    # Store full row for reference
    raw_data: dict


@dataclass
class ExtractionResult:
    """Single replication extraction result from JSON."""
    original_url: str
    replication_url: str
    result: str
    replication_es: str
    replication_es_type: str
    replication_n: str
    confidence: str
    # Store full data for reference
    raw_data: dict


class BenchmarkEvaluator:
    """Evaluates extraction results against ground truth."""

    def __init__(self, ground_truth_csv: Path, papers_dir: Path, tag: str, verbose: bool = False):
        self.ground_truth_csv = ground_truth_csv
        self.papers_dir = papers_dir
        self.tag = tag
        self.verbose = verbose
        self.ground_truth: List[GroundTruth] = []
        self.extractions: List[ExtractionResult] = []

    def log(self, message: str):
        """Print message if verbose mode enabled."""
        if self.verbose:
            print(message, file=sys.stderr)

    def normalize_url(self, url: str) -> str:
        """Normalize DOI URL for comparison."""
        if not url or not isinstance(url, str):
            return ""
        url = url.strip().lower()
        url = url.replace("http://doi.org/", "https://doi.org/")
        url = url.replace("http://dx.doi.org/", "https://doi.org/")
        url = url.replace("https://dx.doi.org/", "https://doi.org/")
        url = url.rstrip("/")
        # Fix double-slash in DOI path (e.g. 10.1037//0022-3514)
        if url.startswith("https://doi.org/"):
            doi_part = url[len("https://doi.org/"):]
            doi_part = doi_part.replace("//", "/")
            url = "https://doi.org/" + doi_part
        return url

    def doi_url_to_folder(self, doi_url: str) -> str:
        """Convert DOI URL to folder name.
        Example: https://doi.org/10.1002/ejsp.2748 -> 10.1002--ejsp.2748
        """
        doi = doi_url.replace("http://doi.org/", "").replace("https://doi.org/", "")
        return doi.replace("/", "--")

    def load_ground_truth(self):
        """Load ground truth CSV into memory."""
        self.log(f"Loading ground truth from {self.ground_truth_csv}")

        with open(self.ground_truth_csv, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                gt = GroundTruth(
                    original_url=row.get("original_url", ""),
                    replication_url=row.get("replication_url", ""),
                    result=row.get("result", ""),
                    replication_es=row.get("replication_es", ""),
                    replication_es_type=row.get("replication_es_type", ""),
                    replication_n=row.get("replication_n", ""),
                    raw_data=row
                )
                self.ground_truth.append(gt)

        self.log(f"Loaded {len(self.ground_truth)} ground truth entries")

    def load_extractions(self):
        """Load all extraction JSONs from tagged subdirectories."""
        self.log(f"Loading extractions from papers_dir with tag: {self.tag}")

        # Iterate through paper directories
        for paper_dir in sorted(self.papers_dir.iterdir()):
            if not paper_dir.is_dir():
                continue

            # Look for tagged subdirectory
            tagged_dir = paper_dir / self.tag
            if not tagged_dir.exists():
                self.log(f"  Skipping {paper_dir.name}: no {self.tag} subdirectory")
                continue

            # Look for result file (try all level suffixes)
            result_file = None
            for suffix in ("_result_full.json", "_result_mid.json", "_result.json"):
                candidate = tagged_dir / f"{paper_dir.name}{suffix}"
                if candidate.exists():
                    result_file = candidate
                    break
            if result_file is None:
                self.log(f"  Skipping {paper_dir.name}: no result file")
                continue

            # Load and parse JSON
            try:
                with open(result_file, encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError as e:
                self.log(f"  ERROR {paper_dir.name}: malformed JSON: {e}")
                continue

            if not data.get("contains_replications"):
                self.log(f"  Skipping {paper_dir.name}: contains_replications=false")
                continue

            # Extract each replication from the array
            for rep in data.get("replications", []):
                ext = ExtractionResult(
                    original_url=rep.get("original_url", ""),
                    replication_url=rep.get("replication_url", ""),
                    result=rep.get("result", ""),
                    replication_es=rep.get("replication_es", ""),
                    replication_es_type=rep.get("replication_es_type", ""),
                    replication_n=rep.get("replication_n", ""),
                    confidence=rep.get("confidence", ""),
                    raw_data=rep
                )
                self.extractions.append(ext)

        self.log(f"Loaded {len(self.extractions)} extracted replications")

    def match_extractions_to_ground_truth(self) -> List[Tuple[GroundTruth, Optional[ExtractionResult]]]:
        """Match each ground truth entry to its extraction (if exists).

        Returns list of (GT, extraction_or_None) tuples.
        """
        self.log("Matching extractions to ground truth entries")

        # Build index of extractions by normalized (original_url, replication_url)
        extraction_index: Dict[Tuple[str, str], List[ExtractionResult]] = {}
        for ext in self.extractions:
            key = (self.normalize_url(ext.original_url), self.normalize_url(ext.replication_url))
            if key not in extraction_index:
                extraction_index[key] = []
            extraction_index[key].append(ext)

        # Match each ground truth entry
        matched_pairs = []
        for gt in self.ground_truth:
            gt_key = (self.normalize_url(gt.original_url), self.normalize_url(gt.replication_url))

            # Look up extraction — exact URL match
            if gt_key in extraction_index and extraction_index[gt_key]:
                ext = extraction_index[gt_key].pop(0)
                matched_pairs.append((gt, ext))
                continue

            # Fallback 1: prefix match for truncated DOIs
            gt_orig_norm, gt_rep_norm = gt_key
            found = False
            if gt_orig_norm and len(gt_orig_norm) >= 25:
                for ext_key, ext_list in extraction_index.items():
                    if not ext_list:
                        continue
                    ext_orig, ext_rep = ext_key
                    if ext_rep != gt_rep_norm:
                        continue
                    if (len(ext_orig) >= 25 and
                        (gt_orig_norm.startswith(ext_orig) or ext_orig.startswith(gt_orig_norm))):
                        ext = ext_list.pop(0)
                        matched_pairs.append((gt, ext))
                        self.log(f"  Prefix match: {gt_orig_norm} ~ {ext_orig}")
                        found = True
                        break
            if found:
                continue

            # Fallback 2: title-based match (same replication paper, similar original title)
            gt_title = gt.raw_data.get("original_title", "").lower().strip()
            if gt_title and len(gt_title) >= 10:
                best_sim = 0
                best_ext_key = None
                for ext_key, ext_list in extraction_index.items():
                    if not ext_list:
                        continue
                    ext_orig, ext_rep = ext_key
                    if ext_rep != gt_rep_norm:
                        continue
                    ext_title = ext_list[0].raw_data.get("original_title", "").lower().strip()
                    if not ext_title:
                        continue
                    sim = SequenceMatcher(None, gt_title, ext_title).ratio()
                    if sim > best_sim and sim >= 0.80:
                        best_sim = sim
                        best_ext_key = ext_key
                if best_ext_key:
                    ext = extraction_index[best_ext_key].pop(0)
                    matched_pairs.append((gt, ext))
                    self.log(f"  Title match (sim={best_sim:.2f}): {gt_title[:60]}")
                    continue

            # No match found
            matched_pairs.append((gt, None))

        matched_count = sum(1 for _, ext in matched_pairs if ext is not None)
        self.log(f"Matched {matched_count}/{len(matched_pairs)} ground truth entries")

        return matched_pairs

    def evaluate_original_url(self, matched_pairs: List[Tuple[GroundTruth, Optional[ExtractionResult]]]) -> Dict:
        """Compute URL retrieval metrics."""
        tp = fp = fn = 0

        for gt, ext in matched_pairs:
            gt_url = self.normalize_url(gt.original_url)

            if ext is None or not ext.original_url:
                fn += 1  # Missing extraction
            else:
                ext_url = self.normalize_url(ext.original_url)
                if ext_url == gt_url:
                    tp += 1  # Correct
                else:
                    fp += 1  # Wrong URL

        total = len(matched_pairs)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        accuracy = tp / total if total > 0 else 0
        extraction_rate = (tp + fp) / total if total > 0 else 0

        return {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "extraction_rate": extraction_rate,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "total": total
        }

    def evaluate_result_classification(self, matched_pairs: List[Tuple[GroundTruth, Optional[ExtractionResult]]]) -> Dict:
        """Compute result classification metrics."""
        y_true = []
        y_pred = []

        for gt, ext in matched_pairs:
            if ext is None or not ext.result:
                continue  # Skip missing extractions
            y_true.append(gt.result)
            y_pred.append(ext.result)

        if not y_true:
            return {"error": "No extractions with result field", "n_evaluated": 0}

        classes = ["success", "failure", "inconclusive", "reversal"]

        # Overall accuracy
        accuracy = accuracy_score(y_true, y_pred)

        # Per-class metrics
        precision, recall, f1, support = precision_recall_fscore_support(
            y_true, y_pred, labels=classes, average=None, zero_division=0
        )

        # Macro/weighted averages
        macro_f1 = np.mean(f1)
        _, _, weighted_f1, _ = precision_recall_fscore_support(
            y_true, y_pred, average='weighted', zero_division=0
        )

        # Confusion matrix
        cm = confusion_matrix(y_true, y_pred, labels=classes)

        # Cohen's kappa
        kappa = cohen_kappa_score(y_true, y_pred)

        return {
            "accuracy": float(accuracy),
            "macro_f1": float(macro_f1),
            "weighted_f1": float(weighted_f1),
            "cohen_kappa": float(kappa),
            "per_class": {
                cls: {
                    "precision": float(precision[i]),
                    "recall": float(recall[i]),
                    "f1": float(f1[i]),
                    "support": int(support[i])
                }
                for i, cls in enumerate(classes)
            },
            "confusion_matrix": cm.tolist(),
            "classes": classes,
            "n_evaluated": len(y_true)
        }

    def evaluate_es_type(self, matched_pairs: List[Tuple[GroundTruth, Optional[ExtractionResult]]]) -> Dict:
        """Compute effect size type classification metrics."""
        tp = fp = fn = 0
        type_counts = {}

        for gt, ext in matched_pairs:
            gt_type = gt.replication_es_type.strip() if gt.replication_es_type else ""
            ext_type = ext.replication_es_type.strip() if (ext and ext.replication_es_type) else ""

            if not gt_type:
                continue  # Skip cases where ground truth is missing

            if not ext_type:
                fn += 1
            elif ext_type == gt_type:
                tp += 1
                type_counts[gt_type] = type_counts.get(gt_type, 0) + 1
            else:
                fp += 1

        total_with_gt = tp + fn + fp
        accuracy = tp / total_with_gt if total_with_gt > 0 else 0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0

        return {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "total_with_ground_truth": total_with_gt,
            "type_distribution": type_counts,
            "note": f"Limited to cases where ground truth has ES type (n={total_with_gt})"
        }

    def evaluate_es_value(self, matched_pairs: List[Tuple[GroundTruth, Optional[ExtractionResult]]]) -> Dict:
        """Compute effect size numerical error metrics."""
        errors = []
        pairs = []

        for gt, ext in matched_pairs:
            gt_es = None
            ext_es = None

            # Try to parse ground truth ES
            if gt.replication_es and str(gt.replication_es).strip():
                try:
                    gt_es = float(gt.replication_es)
                except (ValueError, TypeError):
                    continue

            # Try to parse extracted ES
            if ext and ext.replication_es and str(ext.replication_es).strip():
                try:
                    ext_es = float(ext.replication_es)
                except (ValueError, TypeError):
                    continue

            if gt_es is not None and ext_es is not None:
                errors.append(abs(gt_es - ext_es))
                pairs.append((gt_es, ext_es))

        if not errors:
            return {
                "note": "Insufficient data - no matching ES values",
                "n": 0
            }

        mae = float(np.mean(errors))
        rmse = float(np.sqrt(np.mean([e**2 for e in errors])))

        gt_vals = [p[0] for p in pairs]
        ext_vals = [p[1] for p in pairs]
        correlation = float(np.corrcoef(gt_vals, ext_vals)[0, 1]) if len(pairs) >= 2 else None

        within_005 = sum(1 for e in errors if e <= 0.05) / len(errors)
        within_010 = sum(1 for e in errors if e <= 0.10) / len(errors)
        within_020 = sum(1 for e in errors if e <= 0.20) / len(errors)

        return {
            "mae": mae,
            "rmse": rmse,
            "correlation": correlation,
            "within_0.05": within_005,
            "within_0.10": within_010,
            "within_0.20": within_020,
            "n": len(pairs),
            "note": "Very limited data - interpret with caution" if len(pairs) < 20 else None
        }

    def collect_errors(self, matched_pairs: List[Tuple[GroundTruth, Optional[ExtractionResult]]]) -> List[Dict]:
        """Collect list of misclassifications for error analysis."""
        errors = []

        for gt, ext in matched_pairs:
            if ext is None:
                errors.append({
                    "paper": self.doi_url_to_folder(gt.replication_url),
                    "type": "missing_extraction",
                    "gt_result": gt.result,
                    "ext_result": None,
                    "gt_description": gt.raw_data.get("description", ""),
                    "ext_description": "",
                })
            elif gt.result and ext.result and gt.result != ext.result:
                errors.append({
                    "paper": self.doi_url_to_folder(gt.replication_url),
                    "type": "result_mismatch",
                    "gt_result": gt.result,
                    "ext_result": ext.result,
                    "gt_description": gt.raw_data.get("description", ""),
                    "ext_description": ext.raw_data.get("description", ""),
                })

        return errors

    def run_evaluation(self) -> Dict:
        """Run full evaluation and return results."""
        self.load_ground_truth()
        self.load_extractions()
        matched_pairs = self.match_extractions_to_ground_truth()

        results = {
            "tag": self.tag,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ground_truth_total": len(self.ground_truth),
            "extractions_total": len(self.extractions),
            "matched_pairs": len(matched_pairs),
            "extraction_rate": len(self.extractions) / len(self.ground_truth) if self.ground_truth else 0,
            "metrics": {
                "original_url": self.evaluate_original_url(matched_pairs),
                "result": self.evaluate_result_classification(matched_pairs),
                "replication_es_type": self.evaluate_es_type(matched_pairs),
                "replication_es": self.evaluate_es_value(matched_pairs),
            },
            "errors": self.collect_errors(matched_pairs)
        }

        return results

    def save_results(self, results: Dict, output_dir: Path):
        """Save evaluation results to JSON and text files."""
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save JSON
        json_file = output_dir / f"benchmark_{self.tag}.json"
        with open(json_file, 'w', encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to {json_file}")

        # Save text summary
        summary_file = output_dir / f"benchmark_{self.tag}_summary.txt"
        self.write_summary(results, summary_file)
        print(f"Summary saved to {summary_file}")

        # Save errors CSV
        if results['errors']:
            errors_file = output_dir / f"benchmark_{self.tag}_errors.csv"
            with open(errors_file, 'w', encoding="utf-8", newline='') as f:
                fieldnames = ['paper', 'type', 'gt_result', 'ext_result', 'gt_description', 'ext_description']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results['errors'])
            print(f"Errors saved to {errors_file}")

    def write_summary(self, results: Dict, output_file: Path):
        """Write human-readable summary report."""
        with open(output_file, 'w', encoding="utf-8") as f:
            f.write(f"{'='*70}\n")
            f.write(f"Benchmark Evaluation: {results['tag']}\n")
            f.write(f"{'='*70}\n\n")

            f.write(f"Run Information:\n")
            f.write(f"  Timestamp: {results['timestamp']}\n")
            f.write(f"  Ground Truth Entries: {results['ground_truth_total']}\n")
            f.write(f"  Successful Extractions: {results['extractions_total']}\n")
            f.write(f"  Extraction Rate: {results['extraction_rate']:.1%}\n\n")

            # URL metrics
            url = results['metrics']['original_url']
            f.write(f"{'─'*70}\n")
            f.write(f"Original URL Retrieval\n")
            f.write(f"{'─'*70}\n")
            f.write(f"  Accuracy:  {url['accuracy']:>6.1%}\n")
            f.write(f"  Precision: {url['precision']:>6.1%}\n")
            f.write(f"  Recall:    {url['recall']:>6.1%}\n")
            f.write(f"  F1 Score:  {url['f1']:>6.3f}\n\n")
            f.write(f"  True Positives:  {url['tp']:>4d}\n")
            f.write(f"  False Positives: {url['fp']:>4d}\n")
            f.write(f"  False Negatives: {url['fn']:>4d}\n\n")

            # Result classification
            result = results['metrics']['result']
            if 'error' not in result:
                f.write(f"{'─'*70}\n")
                f.write(f"Result Classification\n")
                f.write(f"{'─'*70}\n")
                f.write(f"  Overall Accuracy: {result['accuracy']:>6.1%}\n")
                f.write(f"  Macro F1:         {result['macro_f1']:>6.3f}\n")
                f.write(f"  Weighted F1:      {result['weighted_f1']:>6.3f}\n")
                f.write(f"  Cohen's Kappa:    {result['cohen_kappa']:>6.3f}\n\n")

                f.write(f"  Per-Class Performance:\n")
                for cls, metrics in result['per_class'].items():
                    f.write(f"    {cls:15s}: P={metrics['precision']:.3f}, R={metrics['recall']:.3f}, "
                           f"F1={metrics['f1']:.3f}, n={metrics['support']}\n")

                f.write(f"\n  Confusion Matrix:\n")
                f.write(f"                Predicted\n")
                f.write(f"                " + "  ".join(f"{c[:4]:>6s}" for c in result['classes']) + "\n")
                cm = result['confusion_matrix']
                for i, cls in enumerate(result['classes']):
                    f.write(f"    Actual {cls[:6]:6s} " + "  ".join(f"{cm[i][j]:>6d}" for j in range(len(cm[i]))) + "\n")
                f.write("\n")

            # ES type
            es_type = results['metrics']['replication_es_type']
            f.write(f"{'─'*70}\n")
            f.write(f"Effect Size Type Classification\n")
            f.write(f"{'─'*70}\n")
            if es_type['total_with_ground_truth'] > 0:
                f.write(f"  Accuracy:  {es_type['accuracy']:>6.1%} ({es_type['tp']}/{es_type['total_with_ground_truth']} correct)\n")
                f.write(f"  Precision: {es_type['precision']:>6.1%}\n")
                f.write(f"  Recall:    {es_type['recall']:>6.1%}\n")
                f.write(f"  Note: {es_type['note']}\n\n")
            else:
                f.write(f"  No ground truth data available\n\n")

            # ES value
            es_val = results['metrics']['replication_es']
            f.write(f"{'─'*70}\n")
            f.write(f"Effect Size Numerical Error\n")
            f.write(f"{'─'*70}\n")
            if es_val['n'] > 0:
                f.write(f"  Mean Absolute Error: {es_val['mae']:.3f}\n")
                f.write(f"  RMSE:                {es_val['rmse']:.3f}\n")
                if es_val['correlation'] is not None:
                    f.write(f"  Correlation:         {es_val['correlation']:.3f}\n")
                f.write(f"  Within ±0.05:        {es_val['within_0.05']:.1%}\n")
                f.write(f"  Within ±0.10:        {es_val['within_0.10']:.1%}\n")
                f.write(f"  Within ±0.20:        {es_val['within_0.20']:.1%}\n")
                f.write(f"  Sample Size:         {es_val['n']}\n")
                if es_val.get('note'):
                    f.write(f"  Note: {es_val['note']}\n")
            else:
                f.write(f"  {es_val['note']}\n")
            f.write(f"\n")

            # Error summary
            f.write(f"{'─'*70}\n")
            f.write(f"Error Analysis\n")
            f.write(f"{'─'*70}\n")
            error_types = {}
            for err in results['errors']:
                error_types[err['type']] = error_types.get(err['type'], 0) + 1

            f.write(f"  Total Errors: {len(results['errors'])}\n\n")
            if error_types:
                f.write(f"  By Type:\n")
                for err_type, count in sorted(error_types.items(), key=lambda x: -x[1]):
                    f.write(f"    {err_type:20s}: {count}\n")

            f.write(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate extraction results against ground truth"
    )
    parser.add_argument(
        "--tag",
        required=True,
        help="Tag of the extraction run to evaluate (e.g., sonnet_test_02_2026)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed progress information"
    )
    parser.add_argument(
        "--small",
        action="store_true",
        help="Use the smaller ground truth subset (ground_truth_data_filtered_small.csv)"
    )

    args = parser.parse_args()

    # Paths
    benchmarking_dir = Path(__file__).parent
    csv_name = "ground_truth_data_filtered_small.csv" if args.small else "ground_truth_enhanced.csv"
    csv_path = benchmarking_dir / csv_name
    papers_dir = benchmarking_dir / "ground_truth_data_filtered_PDFs"
    output_dir = benchmarking_dir / "evaluation_results"

    # Validate inputs
    if not csv_path.exists():
        print(f"Error: Ground truth CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    if not papers_dir.exists():
        print(f"Error: Papers directory not found: {papers_dir}", file=sys.stderr)
        sys.exit(1)

    # Run evaluation
    print(f"Evaluating extraction run: {args.tag}")
    evaluator = BenchmarkEvaluator(csv_path, papers_dir, args.tag, verbose=args.verbose)
    results = evaluator.run_evaluation()
    evaluator.save_results(results, output_dir)

    # Print summary statistics
    print(f"\n{'='*70}")
    print(f"Summary for {args.tag}")
    print(f"{'='*70}")
    print(f"Overall Accuracy: {results['metrics']['result'].get('accuracy', 0):.1%}")
    print(f"URL F1 Score:     {results['metrics']['original_url']['f1']:.3f}")
    print(f"Total Errors:     {len(results['errors'])}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
