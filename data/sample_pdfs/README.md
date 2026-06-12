# Demo Upload PDFs

This directory contains two small PDFs that can be uploaded into the local demo
app:

- `31 nd 200_2012.pdf`
- `2021_S0666.pdf`

Both files are real public decision PDFs included as demo inputs. Their public
source pages are:

- `31 nd 200_2012.pdf`: `https://rozhodnuti.nsoud.cz/Judikatura/judikatura_ns.nsf/WebSearch/57512C4CD90CD53DC1257A78002EEDD6?openDocument`
- `2021_S0666.pdf`: `https://uohs.gov.cz/cs/verejne-zakazky/sbirky-rozhodnuti/detail-17879.html`

The `31 nd 200_2012.pdf` file is useful for showing ordinary deterministic
extraction and document-reference linking. The `2021_S0666.pdf` file is useful
for showing the AI-assisted BRL document-linking path: it contains the reference
`sp. zn. 1 Afs 106/2012`, which has multiple candidate targets in the compact
self-identifier snapshot. BRL review can use the cited decision date
in the surrounding text to select the matching NSS decision.
