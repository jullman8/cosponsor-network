"""
Congress.gov Bulk Data Downloader (BILLSTATUS + BILLSUM XML → JSON)
===================================================================
Downloads BILLSTATUS and BILLSUM bulk XML from GovInfo for congresses
108-119, parses ALL available fields, and outputs comprehensive JSON.

BILLSTATUS: sponsors, cosponsors, actions, committees, subjects,
            CBO estimates, amendments, related bills, text versions, etc.
BILLSUM:    CRS bill summaries (richer text than BILLSTATUS summaries).
            Available for congresses 113-119 only.

No API key needed. No rate limits. Much faster than the API approach.

Usage:
    pip install aiohttp
    python congress-bulk.py                        # all 108-119
    python congress-bulk.py --start 110 --end 119  # custom range
    python congress-bulk.py --congress 118          # single congress
    python congress-bulk.py --fresh                 # clear cache, re-download everything
"""

import argparse
import asyncio
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

try:
    import aiohttp
except ImportError:
    print("ERROR: 'aiohttp' is required. Install with: pip install aiohttp")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GOVINFO_BILLSTATUS = "https://www.govinfo.gov/bulkdata/BILLSTATUS"
GOVINFO_BILLSUM = "https://www.govinfo.gov/bulkdata/BILLSUM"
MAX_CONCURRENT = 50          # no API rate limits, go fast
BILL_TYPES = ["hr", "s", "hjres", "sjres"]

# BILLSUM is only available for congresses 113-119
BILLSUM_MIN_CONGRESS = 113

DEFAULT_START = 108
DEFAULT_END = 119

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = SCRIPT_DIR
CACHE_DIR = OUTPUT_DIR / "cache_bulk"
CACHE_DIR_BILLSUM = OUTPUT_DIR / "cache_billsum"


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------
def text(el, path, default=""):
    """Get text from an XML element path, or default."""
    node = el.find(path)
    return node.text.strip() if node is not None and node.text else default


def text_direct(el, default=""):
    """Get text directly from an element."""
    return el.text.strip() if el is not None and el.text else default


def parse_sponsor(item_el) -> dict:
    if item_el is None:
        return {}
    return {
        "bioguide_id": text(item_el, "bioguideId"),
        "full_name": text(item_el, "fullName"),
        "first_name": text(item_el, "firstName"),
        "last_name": text(item_el, "lastName"),
        "party": text(item_el, "party"),
        "state": text(item_el, "state"),
        "district": text(item_el, "district") or None,
        "is_by_request": text(item_el, "isByRequest"),
    }


def parse_cosponsor(item_el) -> dict:
    return {
        "bioguide_id": text(item_el, "bioguideId"),
        "full_name": text(item_el, "fullName"),
        "first_name": text(item_el, "firstName"),
        "last_name": text(item_el, "lastName"),
        "middle_name": text(item_el, "middleName") or None,
        "party": text(item_el, "party"),
        "state": text(item_el, "state"),
        "district": text(item_el, "district") or None,
        "sponsorship_date": text(item_el, "sponsorshipDate"),
        "is_original_cosponsor": text(item_el, "isOriginalCosponsor") == "True",
    }


def parse_action(item_el) -> dict:
    """Parse a single action element."""
    action = {
        "action_date": text(item_el, "actionDate"),
        "action_time": text(item_el, "actionTime") or None,
        "text": text(item_el, "text"),
        "type": text(item_el, "type"),
        "action_code": text(item_el, "actionCode") or None,
    }

    # Source system
    source = item_el.find("sourceSystem")
    if source is not None:
        action["source_system"] = {
            "code": text(source, "code") or None,
            "name": text(source, "name"),
        }

    # Committees referenced in this action
    committees_el = item_el.find("committees")
    if committees_el is not None:
        action["committees"] = [
            {
                "system_code": text(c, "systemCode"),
                "name": text(c, "name"),
            }
            for c in committees_el.findall("item")
        ]

    # Recorded votes
    votes_el = item_el.find("recordedVotes")
    if votes_el is not None:
        action["recorded_votes"] = [
            {
                "roll_number": text(v, "rollNumber"),
                "url": text(v, "url"),
                "chamber": text(v, "chamber"),
                "congress": text(v, "congress"),
                "date": text(v, "date"),
                "session_number": text(v, "sessionNumber"),
            }
            for v in votes_el.findall("recordedVote")
        ]

    return action


def parse_committee(item_el) -> dict:
    """Parse a single committee element."""
    committee = {
        "system_code": text(item_el, "systemCode"),
        "name": text(item_el, "name"),
        "chamber": text(item_el, "chamber"),
        "type": text(item_el, "type"),
    }

    # Activities
    activities_el = item_el.find("activities")
    if activities_el is not None:
        committee["activities"] = [
            {
                "name": text(a, "name"),
                "date": text(a, "date"),
            }
            for a in activities_el.findall("item")
        ]

    # Subcommittees
    subcomm_el = item_el.find("subcommittees")
    if subcomm_el is not None:
        subcommittees = []
        for sub in subcomm_el.findall("item"):
            sc = {
                "system_code": text(sub, "systemCode"),
                "name": text(sub, "name"),
            }
            sub_activities = sub.find("activities")
            if sub_activities is not None:
                sc["activities"] = [
                    {
                        "name": text(a, "name"),
                        "date": text(a, "date"),
                    }
                    for a in sub_activities.findall("item")
                ]
            subcommittees.append(sc)
        committee["subcommittees"] = subcommittees

    return committee


def parse_related_bill(item_el) -> dict:
    """Parse a single related bill element."""
    rb = {
        "title": text(item_el, "title"),
        "congress": text(item_el, "congress"),
        "number": text(item_el, "number"),
        "type": text(item_el, "type"),
    }

    la = item_el.find("latestAction")
    if la is not None:
        rb["latest_action"] = {
            "action_date": text(la, "actionDate"),
            "action_time": text(la, "actionTime") or None,
            "text": text(la, "text"),
        }

    rel_el = item_el.find("relationshipDetails")
    if rel_el is not None:
        rb["relationship_details"] = [
            {
                "type": text(r, "type"),
                "identified_by": text(r, "identifiedBy"),
            }
            for r in rel_el.findall("item")
        ]

    return rb


def parse_cbo_estimate(item_el) -> dict:
    """Parse a single CBO cost estimate element."""
    return {
        "pub_date": text(item_el, "pubDate"),
        "title": text(item_el, "title"),
        "url": text(item_el, "url"),
        "description": text(item_el, "description") or None,
    }


def parse_summary(item_el) -> dict:
    """Parse a single summary element."""
    return {
        "version_code": text(item_el, "versionCode"),
        "action_date": text(item_el, "actionDate"),
        "action_desc": text(item_el, "actionDesc"),
        "text": text(item_el, "text"),
        "update_date": text(item_el, "updateDate"),
    }


def parse_title(item_el) -> dict:
    """Parse a single title element."""
    return {
        "title_type": text(item_el, "titleType"),
        "title": text(item_el, "title"),
        "chamber_code": text(item_el, "chamberCode") or None,
        "chamber_name": text(item_el, "chamberName") or None,
        "bill_text_version_name": text(item_el, "billTextVersionName") or None,
        "bill_text_version_code": text(item_el, "billTextVersionCode") or None,
    }


def parse_text_version(item_el) -> dict:
    """Parse a single text version element."""
    tv = {
        "type": text(item_el, "type"),
        "date": text(item_el, "date") or None,
    }
    formats_el = item_el.find("formats")
    if formats_el is not None:
        tv["formats"] = [
            {"url": text(f, "url")}
            for f in formats_el.findall("item")
            if text(f, "url")
        ]
    return tv


def parse_bill_xml(xml_bytes: bytes) -> dict | None:
    """Parse a BILLSTATUS XML file into a comprehensive bill record dict."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    bill = root.find("bill")
    if bill is None:
        return None

    congress = int(text(bill, "number", "0") and text(bill, "congress", "0"))
    bill_type = text(bill, "type", "").lower()
    bill_number = text(bill, "number")

    if not bill_number or not bill_type:
        return None

    bill_id = f"{bill_type}-{bill_number}-{congress}"

    # ── Sponsor ──
    sponsors_el = bill.find("sponsors")
    sponsor = {}
    if sponsors_el is not None:
        first_item = sponsors_el.find("item")
        if first_item is not None:
            sponsor = parse_sponsor(first_item)

    # ── Cosponsors ──
    cosponsors_el = bill.find("cosponsors")
    cosponsors = []
    if cosponsors_el is not None:
        for item in cosponsors_el.findall("item"):
            cosponsors.append(parse_cosponsor(item))

    # ── Bipartisanship ──
    parties = set()
    if sponsor.get("party"):
        parties.add(sponsor["party"])
    for c in cosponsors:
        if c.get("party"):
            parties.add(c["party"])
    is_bipartisan = ("D" in parties) and ("R" in parties)

    # ── Latest action ──
    la = bill.find("latestAction")
    latest_action = {
        "date": text(la, "actionDate") if la is not None else "",
        "text": text(la, "text") if la is not None else "",
    }

    # ── Policy area ──
    pa = bill.find("policyArea")
    policy_area = text(pa, "name") if pa is not None else ""

    # ── All actions ──
    actions_el = bill.find("actions")
    actions = []
    if actions_el is not None:
        for item in actions_el.findall("item"):
            actions.append(parse_action(item))

    # ── Committees ──
    committees_el = bill.find("committees")
    committees = []
    if committees_el is not None:
        for item in committees_el.findall("item"):
            committees.append(parse_committee(item))

    # ── Related bills ──
    related_el = bill.find("relatedBills")
    related_bills = []
    if related_el is not None:
        for item in related_el.findall("item"):
            related_bills.append(parse_related_bill(item))

    # ── CBO cost estimates ──
    cbo_el = bill.find("cboCostEstimates")
    cbo_cost_estimates = []
    if cbo_el is not None:
        for item in cbo_el.findall("item"):
            cbo_cost_estimates.append(parse_cbo_estimate(item))

    # ── Subjects ──
    subjects_el = bill.find("subjects")
    legislative_subjects = []
    if subjects_el is not None:
        leg_subj_el = subjects_el.find("legislativeSubjects")
        if leg_subj_el is not None:
            for item in leg_subj_el.findall("item"):
                legislative_subjects.append(text(item, "name"))

    # ── Summaries ──
    summaries_el = bill.find("summaries")
    summaries = []
    if summaries_el is not None:
        for item in summaries_el.findall("item"):
            summaries.append(parse_summary(item))

    # ── Titles ──
    titles_el = bill.find("titles")
    titles = []
    if titles_el is not None:
        for item in titles_el.findall("item"):
            titles.append(parse_title(item))

    # ── Text versions ──
    text_versions_el = bill.find("textVersions")
    text_versions = []
    if text_versions_el is not None:
        for item in text_versions_el.findall("item"):
            text_versions.append(parse_text_version(item))

    # ── Constitutional authority statement ──
    cdata_el = bill.find("cdata")
    constitutional_authority = ""
    if cdata_el is not None:
        constitutional_authority = text(
            cdata_el, "constitutionalAuthorityStatementText"
        )

    # ── Laws (if enacted) ──
    laws_el = bill.find("laws")
    laws = []
    if laws_el is not None:
        for item in laws_el.findall("item"):
            laws.append({
                "type": text(item, "type"),
                "number": text(item, "number"),
            })

    # ── Notes ──
    notes_el = bill.find("notes")
    notes = []
    if notes_el is not None:
        for item in notes_el.findall("item"):
            notes.append({"text": text(item, "text")})

    # ── Amendments ──
    amendments_el = bill.find("amendments")
    amendments = []
    if amendments_el is not None:
        for item in amendments_el.findall("item"):
            amend = {
                "number": text(item, "number"),
                "congress": text(item, "congress"),
                "type": text(item, "type"),
                "description": text(item, "description") or None,
                "purpose": text(item, "purpose") or None,
                "update_date": text(item, "updateDate") or None,
            }
            amend_la = item.find("latestAction")
            if amend_la is not None:
                amend["latest_action"] = {
                    "action_date": text(amend_la, "actionDate"),
                    "text": text(amend_la, "text"),
                }
            # Amendment links (to other bills/amendments)
            amend_links = item.find("links")
            if amend_links is not None:
                amend["links"] = [
                    {
                        "name": text(lk, "name"),
                        "url": text(lk, "url"),
                    }
                    for lk in amend_links.findall("item")
                ]
            amendments.append(amend)

    return {
        "bill_id": bill_id,
        "bill_type": bill_type,
        "bill_number": bill_number,
        "congress": congress,
        "title": text(bill, "title"),
        "titles": titles,
        "introduced_date": text(bill, "introducedDate"),
        "update_date": text(bill, "updateDate"),
        "origin_chamber": text(bill, "originChamber"),
        "latest_action": latest_action,
        "policy_area": policy_area,
        "legislative_subjects": legislative_subjects,
        "sponsor": sponsor,
        "cosponsors": cosponsors,
        "cosponsor_count": len(cosponsors),
        "is_bipartisan": is_bipartisan,
        "parties_involved": sorted(parties),
        "actions": actions,
        "action_count": len(actions),
        "committees": committees,
        "related_bills": related_bills,
        "cbo_cost_estimates": cbo_cost_estimates,
        "summaries": summaries,
        "text_versions": text_versions,
        "amendments": amendments,
        "amendment_count": len(amendments),
        "laws": laws,
        "notes": notes,
        "constitutional_authority_statement": constitutional_authority or None,
    }


# ---------------------------------------------------------------------------
# BILLSUM XML parser
# ---------------------------------------------------------------------------
def parse_billsum_xml(xml_bytes: bytes) -> dict | None:
    """
    Parse a BILLSUM XML file into a dict of CRS summaries.

    Returns {bill_key: [summary_dicts]} where bill_key is like "hr-1-118".
    A single BILLSUM file typically covers one bill but may have multiple
    summary versions (e.g., "Introduced in House", "Passed House", etc.).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    results = {}

    # Root is <BillSummaries>, children are <item> elements
    for item in root.findall("item"):
        congress = item.get("congress", "")
        measure_type = item.get("measure-type", "").lower()
        measure_number = item.get("measure-number", "")

        if not congress or not measure_type or not measure_number:
            continue

        bill_key = f"{measure_type}-{measure_number}-{congress}"

        summaries = []
        for summary_el in item.findall("summary"):
            action_date = ""
            ad_el = summary_el.find("action-date")
            if ad_el is not None and ad_el.text:
                action_date = ad_el.text.strip()

            action_desc = ""
            adesc_el = summary_el.find("action-desc")
            if adesc_el is not None and adesc_el.text:
                action_desc = adesc_el.text.strip()

            summary_text = ""
            st_el = summary_el.find("summary-text")
            if st_el is not None and st_el.text:
                summary_text = st_el.text.strip()

            summaries.append({
                "action_date": action_date,
                "action_desc": action_desc,
                "text": summary_text,
                "current_chamber": summary_el.get("currentChamber", ""),
                "update_date": summary_el.get("update-date", ""),
            })

        if summaries:
            results[bill_key] = summaries

    return results


# ---------------------------------------------------------------------------
# Directory listing parser
# ---------------------------------------------------------------------------
def parse_directory_xml(xml_bytes: bytes) -> list[str]:
    """Parse a GovInfo directory listing XML, return list of file URLs."""
    root = ET.fromstring(xml_bytes)
    urls = []
    for file_el in root.iter("file"):
        folder = file_el.find("folder")
        if folder is not None and folder.text == "true":
            continue
        link = file_el.find("link")
        if link is not None and link.text:
            urls.append(link.text.strip())
    return urls


# ---------------------------------------------------------------------------
# Async downloader
# ---------------------------------------------------------------------------
class BulkDownloader:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.session = None
        self.downloaded = 0
        self.errors = 0
        self.total = 0
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(
            limit=max(MAX_CONCURRENT, 50),
            limit_per_host=max(MAX_CONCURRENT, 50),
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        timeout = aiohttp.ClientTimeout(total=120, connect=15)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"Accept": "application/xml"},
        )
        return self

    async def __aexit__(self, *exc):
        if self.session:
            await self.session.close()

    async def fetch(self, url: str) -> bytes | None:
        async with self.semaphore:
            for attempt in range(3):
                try:
                    async with self.session.get(url) as resp:
                        if resp.status == 200:
                            return await resp.read()
                        if resp.status >= 500:
                            await asyncio.sleep(1 * (attempt + 1))
                            continue
                        return None
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if attempt < 2:
                        await asyncio.sleep(1 * (attempt + 1))
        return None

    async def fetch_and_parse_bill(self, url: str) -> dict | None:
        data = await self.fetch(url)
        if data is None:
            async with self._lock:
                self.errors += 1
            return None

        record = parse_bill_xml(data)

        async with self._lock:
            self.downloaded += 1
            if self.downloaded % 500 == 0 or self.downloaded == self.total:
                print(f"    {self.downloaded:>6}/{self.total} bills parsed")

        return record

    async def fetch_and_parse_billsum(self, url: str) -> dict | None:
        """Download and parse a BILLSUM XML file."""
        data = await self.fetch(url)
        if data is None:
            async with self._lock:
                self.errors += 1
            return None

        result = parse_billsum_xml(data)

        async with self._lock:
            self.downloaded += 1
            if self.downloaded % 500 == 0 or self.downloaded == self.total:
                print(f"    {self.downloaded:>6}/{self.total} summaries parsed")

        return result

    async def get_file_list(self, base_url: str, congress: int, bill_type: str) -> list[str]:
        url = f"{base_url}/{congress}/{bill_type}"
        data = await self.fetch(url)
        if data is None:
            return []
        return parse_directory_xml(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def congress_to_years(congress: int) -> str:
    start_year = 1789 + (congress - 1) * 2
    return f"{start_year}-{start_year + 2}"


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def load_cache(congress: int) -> dict:
    cache_file = CACHE_DIR / f"congress_{congress}_bulk.json"
    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(congress: int, cache: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"congress_{congress}_bulk.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def load_billsum_cache(congress: int) -> dict:
    cache_file = CACHE_DIR_BILLSUM / f"congress_{congress}_billsum.json"
    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_billsum_cache(congress: int, cache: dict):
    CACHE_DIR_BILLSUM.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR_BILLSUM / f"congress_{congress}_billsum.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def clear_cache():
    """Remove all cached files to force re-download."""
    for cache_dir, pattern, label in [
        (CACHE_DIR, "congress_*_bulk.json", "BILLSTATUS"),
        (CACHE_DIR_BILLSUM, "congress_*_billsum.json", "BILLSUM"),
    ]:
        if cache_dir.exists():
            count = 0
            for f in cache_dir.glob(pattern):
                f.unlink()
                count += 1
            print(f"  Cleared {count} {label} cache files from {cache_dir}")
        else:
            print(f"  No {label} cache directory found at {cache_dir}")


# ---------------------------------------------------------------------------
# Single congress download
# ---------------------------------------------------------------------------
async def download_billsum(dl: BulkDownloader, congress: int) -> dict:
    """
    Download BILLSUM data for a congress.

    Returns {bill_key: [summary_dicts]} merged from all bill types.
    """
    if congress < BILLSUM_MIN_CONGRESS:
        print(f"  BILLSUM: not available for congress {congress} (starts at {BILLSUM_MIN_CONGRESS})")
        return {}

    # All bill types in BILLSUM (includes resolutions too, we filter to ours)
    billsum_types = BILL_TYPES  # hr, s, hjres, sjres

    print(f"  Phase 3: Downloading BILLSUM (CRS summaries)...")

    # Get file listings
    listings = await asyncio.gather(
        *[dl.get_file_list(GOVINFO_BILLSUM, congress, bt) for bt in billsum_types]
    )

    billsum_cache = load_billsum_cache(congress)
    all_urls = []
    for bt, urls in zip(billsum_types, listings):
        all_urls.extend(urls)

    urls_to_fetch = []
    cached_count = 0
    for url in all_urls:
        fname = url.rsplit("/", 1)[-1]
        cache_key = fname.replace(".xml", "")
        if cache_key in billsum_cache:
            cached_count += 1
        else:
            urls_to_fetch.append((url, cache_key))

    print(f"    BILLSUM: {len(all_urls)} files ({len(urls_to_fetch)} new, {cached_count} cached)")

    dl.downloaded = 0
    dl.errors = 0
    dl.total = len(urls_to_fetch)

    if urls_to_fetch:
        tasks = [dl.fetch_and_parse_billsum(url) for url, _ in urls_to_fetch]
        results = await asyncio.gather(*tasks)

        for (url, cache_key), result in zip(urls_to_fetch, results):
            if result is not None:
                billsum_cache[cache_key] = result

        save_billsum_cache(congress, billsum_cache)

    # Merge all summaries into a single {bill_key: [summaries]} dict
    merged = {}
    for cache_key, val in billsum_cache.items():
        if isinstance(val, dict):
            for bill_key, sums in val.items():
                if bill_key in merged:
                    # De-duplicate by action_date + action_desc
                    existing = {(s["action_date"], s["action_desc"]) for s in merged[bill_key]}
                    for s in sums:
                        if (s["action_date"], s["action_desc"]) not in existing:
                            merged[bill_key].append(s)
                else:
                    merged[bill_key] = list(sums)

    print(f"    BILLSUM: {len(merged)} bills with CRS summaries")
    return merged


async def download_congress(dl: BulkDownloader, congress: int) -> dict | None:
    years = congress_to_years(congress)
    label = f"{ordinal(congress)} Congress ({years})"

    print(f"\n{'#'*60}")
    print(f"  {label}")
    print(f"{'#'*60}")

    cache = load_cache(congress)
    if cache:
        print(f"  {len(cache)} BILLSTATUS bills already cached.")

    t0 = time.monotonic()

    # Phase 1: Get BILLSTATUS file listings for all bill types concurrently
    print(f"  Phase 1: Listing BILLSTATUS files...")
    listings = await asyncio.gather(
        *[dl.get_file_list(GOVINFO_BILLSTATUS, congress, bt) for bt in BILL_TYPES]
    )

    all_urls = []
    for bt, urls in zip(BILL_TYPES, listings):
        print(f"    {bt.upper():>6}: {len(urls):>6} files")
        all_urls.extend(urls)

    total_files = len(all_urls)
    cached_count = 0
    urls_to_fetch = []

    for url in all_urls:
        fname = url.rsplit("/", 1)[-1]  # BILLSTATUS-118hr1.xml
        cache_key = fname.replace(".xml", "")
        if cache_key in cache:
            cached_count += 1
        else:
            urls_to_fetch.append((url, cache_key))

    print(f"    Total: {total_files} files ({len(urls_to_fetch)} new, {cached_count} cached)")

    if total_files == 0:
        print(f"    No files found. Skipping.")
        return None

    # Phase 2: Download and parse all BILLSTATUS XML files concurrently
    print(f"  Phase 2: Downloading & parsing BILLSTATUS XML...")
    dl.downloaded = 0
    dl.errors = 0
    dl.total = len(urls_to_fetch)

    if urls_to_fetch:
        tasks = [dl.fetch_and_parse_bill(url) for url, _ in urls_to_fetch]
        results = await asyncio.gather(*tasks)

        # Store in cache
        for (url, cache_key), record in zip(urls_to_fetch, results):
            if record is not None:
                cache[cache_key] = record

        save_cache(congress, cache)

    # Phase 3: Download BILLSUM (CRS summaries) — congresses 113+ only
    billsum_data = await download_billsum(dl, congress)

    # Build final bill list from cache (includes previously cached + new)
    processed_bills = [v for v in cache.values() if isinstance(v, dict) and "bill_id" in v]

    # Merge BILLSUM summaries into bill records
    billsum_matched = 0
    for bill in processed_bills:
        bill_key = bill.get("bill_id", "")
        if bill_key in billsum_data:
            bill["crs_summaries"] = billsum_data[bill_key]
            billsum_matched += 1
        else:
            bill["crs_summaries"] = []

    elapsed = time.monotonic() - t0
    total = len(processed_bills)
    bipartisan = sum(1 for b in processed_bills if b.get("is_bipartisan"))
    with_cosponsors = sum(1 for b in processed_bills if b.get("cosponsor_count", 0) > 0)
    became_law = sum(
        1 for b in processed_bills
        if "Became Public Law" in b.get("latest_action", {}).get("text", "")
    )
    pct = f"{bipartisan / total * 100:.1f}%" if total else "N/A"

    print(f"\n  Done in {elapsed:.1f}s")
    print(f"  Bills: {total} | Bipartisan: {bipartisan} ({pct}) "
          f"| Became law: {became_law} | CRS summaries matched: {billsum_matched} "
          f"| Errors: {dl.errors}")

    return {
        "metadata": {
            "congress": congress,
            "years": years,
            "bill_types_included": BILL_TYPES,
            "bill_types_excluded": ["hres", "sres", "hconres", "sconres"],
            "exclusion_rationale": (
                "Simple and concurrent resolutions excluded (non-binding). "
                "Joint resolutions included (carry force of law)."
            ),
            "total_bills": total,
            "bipartisan_bills": bipartisan,
            "bills_with_cosponsors": with_cosponsors,
            "bills_became_law": became_law,
            "bills_with_crs_summaries": billsum_matched,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "download_duration_seconds": round(elapsed, 1),
            "data_source_billstatus": "https://www.govinfo.gov/bulkdata/BILLSTATUS",
            "data_source_billsum": "https://www.govinfo.gov/bulkdata/BILLSUM",
            "schema_version": "comprehensive_v3",
        },
        "bills": processed_bills,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def download_all(start: int, end: int):
    congress_range = list(range(start, end + 1))
    total_congresses = len(congress_range)
    summaries = []

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bills_dir = OUTPUT_DIR / "bills_by_congress"
    bills_dir.mkdir(parents=True, exist_ok=True)

    wall_start = time.monotonic()

    print(f"\n{'='*60}")
    print(f"  GovInfo Bulk BILLSTATUS Downloader (comprehensive)")
    print(f"  Range: {ordinal(start)} to {ordinal(end)} Congress ({total_congresses} total)")
    print(f"  Concurrency: {MAX_CONCURRENT}")
    print(f"{'='*60}")

    async with BulkDownloader() as dl:
        for idx, congress in enumerate(congress_range, 1):
            print(f"\n  [{idx}/{total_congresses}]", end="")
            result = await download_congress(dl, congress)

            if result is None:
                summaries.append({
                    "congress": congress,
                    "years": congress_to_years(congress),
                    "total_bills": 0,
                    "status": "no_data",
                })
                continue

            # Write JSON file
            congress_file = bills_dir / f"bills_{congress}.json"
            with open(congress_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            size_kb = os.path.getsize(congress_file) / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
            print(f"  Saved: {congress_file.name} ({size_str})")

            meta = result["metadata"]
            total = meta["total_bills"]
            bipartisan = meta["bipartisan_bills"]
            became_law = meta["bills_became_law"]
            summaries.append({
                "congress": congress,
                "years": congress_to_years(congress),
                "total_bills": total,
                "bipartisan_bills": bipartisan,
                "bipartisan_pct": round(bipartisan / total * 100, 1) if total else 0,
                "bills_with_cosponsors": meta["bills_with_cosponsors"],
                "bills_became_law": became_law,
                "bills_with_crs_summaries": meta.get("bills_with_crs_summaries", 0),
                "status": "complete",
            })

    total_elapsed = time.monotonic() - wall_start

    # Summary
    grand_total = sum(s.get("total_bills", 0) for s in summaries)
    grand_bipartisan = sum(s.get("bipartisan_bills", 0) for s in summaries)
    grand_became_law = sum(s.get("bills_became_law", 0) for s in summaries)

    summary_output = {
        "metadata": {
            "congress_range": f"{start}-{end}",
            "total_congresses": total_congresses,
            "total_bills_all_congresses": grand_total,
            "total_bipartisan_all_congresses": grand_bipartisan,
            "overall_bipartisan_pct": round(
                grand_bipartisan / grand_total * 100, 1
            ) if grand_total else 0,
            "total_became_law": grand_became_law,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "total_download_duration_seconds": round(total_elapsed, 1),
            "schema_version": "comprehensive_v2",
        },
        "congresses": summaries,
    }

    summary_file = OUTPUT_DIR / "all_congresses_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_output, f, indent=2, ensure_ascii=False)

    print(f"\n\n{'='*60}")
    print(f"  ALL DONE in {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"{'='*60}")
    print(f"\n  {'Congress':<12} {'Years':<12} {'Bills':>8} {'Bipartisan':>12} {'Pct':>8} {'Laws':>8} {'CRS Sum':>8}")
    print(f"  {'-'*68}")
    for s in summaries:
        if s["status"] == "no_data":
            print(f"  {ordinal(s['congress']):<12} {s['years']:<12} {'(no data)':>8}")
        else:
            crs = s.get("bills_with_crs_summaries", 0)
            print(
                f"  {ordinal(s['congress']):<12} {s['years']:<12} "
                f"{s['total_bills']:>8} {s['bipartisan_bills']:>12} "
                f"{s['bipartisan_pct']:>7.1f}% {s['bills_became_law']:>8} "
                f"{crs:>8}"
            )
    print(f"  {'-'*68}")
    grand_crs = sum(s.get("bills_with_crs_summaries", 0) for s in summaries)
    if grand_total:
        print(
            f"  {'TOTAL':<12} {'':12} {grand_total:>8} "
            f"{grand_bipartisan:>12} "
            f"{grand_bipartisan / grand_total * 100:>7.1f}% "
            f"{grand_became_law:>8} "
            f"{grand_crs:>8}"
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Download BILLSTATUS bulk XML from GovInfo (congresses 108-119)."
    )
    parser.add_argument("--congress", type=int, default=None)
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    parser.add_argument(
        "--fresh", action="store_true",
        help="Clear cache and re-download everything from scratch",
    )
    args = parser.parse_args()

    if args.fresh:
        print("Clearing cache for fresh download...")
        if args.congress is not None:
            # Only clear the specific congress cache
            for cache_dir, pattern in [
                (CACHE_DIR, f"congress_{args.congress}_bulk.json"),
                (CACHE_DIR_BILLSUM, f"congress_{args.congress}_billsum.json"),
            ]:
                cache_file = cache_dir / pattern
                if cache_file.exists():
                    cache_file.unlink()
                    print(f"  Cleared {cache_file.name}")
        else:
            clear_cache()

    if args.congress is not None:
        start, end = args.congress, args.congress
    else:
        start, end = args.start, args.end

    if start < 108 or end > 119:
        print("ERROR: Bulk data only available for congresses 108-119")
        sys.exit(1)
    if start > end:
        print(f"ERROR: --start ({start}) must be <= --end ({end})")
        sys.exit(1)

    asyncio.run(download_all(start, end))


if __name__ == "__main__":
    main()
