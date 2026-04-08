# AdGuard DNS Rewriter

Bulk-add DNS rewrite rules to [AdGuard Home](https://adguard.com/en/adguard-home/overview.html) from a remote domain list.

It fetches a plain-text file of domains, compares them against existing rewrite rules, and adds any missing entries in parallel.

## Requirements

- Python 3.10+
- `requests`

## Setup

```bash
cp .env.example .env   # edit with your values
pip install requests
```

## Configuration

| Variable       | Required | Description                                             |
| -------------- | -------- | ------------------------------------------------------- |
| `ADGUARD_URL`  | yes      | Base URL of your AdGuard Home instance                  |
| `ADGUARD_USER` | yes      | Admin username                                          |
| `ADGUARD_PASS` | yes      | Admin password                                          |
| `REWRITE_IP`   | yes      | IP address to rewrite domains to                        |
| `DOMAINS_URL`  | yes      | URL of a plain-text domain list (one per line)          |
| `DRY_RUN`      | no       | Set to `true` to skip actual changes (default: `false`) |
| `WORKERS`      | no       | Number of parallel workers (default: `10`)              |

## Usage

```bash
# load env and run
source .env && python main.py

# or use make
make run
make dry-run
```

## Domain list format

One domain per line. Empty lines and lines starting with `#` are ignored.

```text
ads.example.com
tracker.example.net
# this is a comment
telemetry.example.org
```

---

## Support 💛

[![Donate with Bitcoin](https://img.shields.io/badge/Bitcoin-bc1qmmh6vt366yzjt3grjxjjqynrrxs3frun8gnxrz-orange)](https://donatebadges.ir/donate/Bitcoin/bc1qmmh6vt366yzjt3grjxjjqynrrxs3frun8gnxrz) [![Donate with Ethereum](https://img.shields.io/badge/Ethereum-0x0831bD72Ea8904B38Be9D6185Da2f930d6078094-blueviolet)](https://donatebadges.ir/donate/Ethereum/0x0831bD72Ea8904B38Be9D6185Da2f930d6078094)

<div><a href="https://payping.ir/@hatamiarash7"><img src="https://cdn.payping.ir/statics/Payping-logo/Trust/blue.svg" height="128" width="128"></a></div>

## Contributing 🤝

Don't be shy and reach out to us if you want to contribute 😉

1. Fork it!
2. Create your feature branch: `git checkout -b my-new-feature`
3. Commit your changes: `git commit -am 'Add some feature'`
4. Push to the branch: `git push origin my-new-feature`
5. Submit a pull request

## Issues

Each project may have many problems. Contributing to the better development of this project by reporting them. 👍
