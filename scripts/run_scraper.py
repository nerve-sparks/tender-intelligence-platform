"""
Manual GeM scrape + analysis (same pipeline as the "Scrape & Analyze" button).

    python -m scripts.run_scraper                                  # profile keyword searches + latest listing
    python -m scripts.run_scraper --max-pages 5 --max-bids 100     # bigger general listing
    python -m scripts.run_scraper --keyword "artificial intelligence" --keyword "software development"
    python -m scripts.run_scraper --no-keywords                    # general listing only
    python -m scripts.run_scraper --test                           # scrape only, print bids, no DB writes
"""

import argparse
import asyncio

from app.jobs.scrape_job import run_scrape_job
from app.scraper.gem_scraper import GemScraper


async def test_scrape(max_pages: int, max_bids: int, keywords) -> None:
    async with GemScraper() as scraper:
        for keyword in keywords or [None]:
            bids = await scraper.scrape_bids(max_pages=max_pages, max_bids=max_bids, keyword=keyword)
            print(f"[{keyword or 'latest'}] {len(bids)} bids")
            for bid in bids[:10]:
                print(f"  {bid.get('bid_id')} | {bid.get('items')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=2, help="pages of the general listing")
    parser.add_argument("--max-bids", type=int, default=20, help="bids from the general listing")
    parser.add_argument("--keyword", action="append", help="GeM search term (repeatable); default: company profiles")
    parser.add_argument("--no-keywords", action="store_true", help="skip keyword searches")
    parser.add_argument("--test", action="store_true", help="scrape only, no database writes")
    args = parser.parse_args()

    if args.test:
        asyncio.run(test_scrape(args.max_pages, args.max_bids, args.keyword))
    else:
        asyncio.run(
            run_scrape_job(
                keywords=args.keyword,
                use_keywords=not args.no_keywords,
                max_pages=args.max_pages,
                max_bids=args.max_bids,
            )
        )
