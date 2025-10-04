# offline Wikipedia Viewer

A lightweight offline Wikipedia browser built with Python and Flask. Search and read Wikipedia articles without an internet connection.

## Features

- Full-text search across millions of articles
- Progressive indexing - start searching immediately while the index loads
- Clean, responsive interface
- Direct access to compressed Wikipedia dumps (no extraction needed)

## Requirements

- Python 3.7+
- ~8-10 GB RAM
- ~22 GB disk space for Wikipedia dump files

## Setup

### 1. Download Wikipedia Dumps

Download these files from [https://dumps.wikimedia.org/enwiki/](https://dumps.wikimedia.org/enwiki/):

- `enwiki-YYYYMMDD-pages-articles-multistream-index.txt.bz2`
- `enwiki-YYYYMMDD-pages-articles-multistream.xml.bz2`

Keep the files compressed - the application reads them directly in `.bz2` format.

### 2. Install

```bash
# Clone the repository
git clone https://github.com/yourusername/offline-wikipedia-viewer.git
cd offline-wikipedia-viewer

# Create data directory and move downloaded files
mkdir -p data
mv path/to/enwiki-*.bz2 data/

# Install dependencies
pip install -r requirements.txt
```

### 3. Run

```bash
python wikipedia_offline.py
```

Open your browser to `http://127.0.0.1:5000/`

The application will index articles in the background. Search becomes available immediately with results improving as more articles are indexed.

## Usage

- Type at least 2 characters to search
- Results appear as you type
- Click any result to view the full article
- Use the back link to return to search

## Project Structure

```
offline-wikipedia-viewer/
├── data/                    # Wikipedia dump files (not in repo)
├── wikipedia_offline.py     # Main application
├── requirements.txt         # Python dependencies
├── README.md
├── LICENSE.txt
└── .gitignore
```

## Screenshot
[Offline Wikipedia Viewer Interface](screenshot.png)

## Applications
Offline LLM, Apocalypse, War, Education, Research, Emergency Services, Remote Work, Field Studies, Disaster Response, Healthcare.

## Technical Details

The application loads the Wikipedia index into memory for fast lookups, while articles are extracted on-demand from the compressed dump file. An LRU cache keeps recently viewed articles in memory.

## Limitations

- Text-only (images not included)
- Some complex Wikipedia formatting may not render perfectly
- External links require internet connection

## License

MIT License - see [LICENSE.txt](LICENSE.txt) for details.
