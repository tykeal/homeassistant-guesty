<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Guesty

A [Home Assistant](https://www.home-assistant.io/) custom integration
for the [Guesty](https://www.guesty.com/) property management platform.

## Overview

This integration connects Home Assistant to the Guesty Open API using
OAuth 2.0 Client Credentials authentication. It provides property
management automation capabilities for short-term rental operators.

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "Guesty" and install
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration → Guesty

### Manual

1. Copy `custom_components/guesty` to your HA `custom_components/`
   directory
2. Restart Home Assistant
3. Go to Settings → Devices & Services → Add Integration → Guesty

## Configuration

You will need Guesty API credentials (Client ID and Client Secret)
from the Guesty developer portal.

## License

This project is licensed under the Apache License 2.0. See
[LICENSE](LICENSE) for details.
