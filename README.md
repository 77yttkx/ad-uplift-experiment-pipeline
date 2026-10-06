# ad-uplift-experiment-pipeline

Status: In Progress — A1 data foundation

## Dataset

- Source: [Criteo Uplift Prediction Dataset](https://ailab.criteo.com/?p=859)
- License: CC BY-NC-SA 4.0 (non-commercial). The dataset is not included in this repo and is subject to its own license.
- Version used: v2.1 (13,979,592-row version on the Criteo page)
- File: `criteo-uplift-v2.1.csv`, 3,248,115,221 bytes (~3.0 GiB), SHA-256 `e4d7c710ca1f38e523309d0f8a0745d1b53e7392d51f20d1088b6cfeaef222ef`
- Rows: 13,979,592 (13,979,593 lines including header); columns: 16 (f0–f11, treatment, conversion, visit, exposure)

## License and Data Use

Code in this repository is released under the MIT License (see `LICENSE`). The raw dataset and any user-level tables derived from it are not redistributed here. Published results are aggregates only, and any assumed ad cost, conversion value or profit is labeled as simulation / assumption.

## Citation

Data: Criteo Uplift Prediction Dataset, Criteo AI Lab.

```bibtex
@inproceedings{Diemert2018,
  author    = {Diemert, Eustache and Betlei, Artem and Renaudin, Christophe and Amini, Massih-Reza},
  title     = {A Large Scale Benchmark for Uplift Modeling},
  booktitle = {Proceedings of the AdKDD and TargetAd Workshop, KDD},
  address   = {London, United Kingdom},
  year      = {2018},
  publisher = {ACM}
}
```
