# Site monitor

This program monitors given web pages (HTTP URLs) responses and corresponding page content requirements from a configuration file.

Based period set in configuration file (or commandline option) the program will send GET request asynchronously to all of the given URLs. The results are written to a produced log file (sqlite DB file). The program also implements simple HTTP server interface in the same process for showing the status of each monitored web site.

## Requirements

Developed with Python 3.10.0, other versions not tested.

## Installation

Install Python. Navigate to the scrip's directory in terminal.

To install dependencies:
If using Poetry, run:
```bash
poetry install
```

Otherwise:
```bash
pip install -r requirements.txt
```

## Configuration

The expected configuration file format is `json`, e.g. `config.json` in the folder of the program, with following example structure:
```json
{
    "interval": 5,
    "targets": [
        {
            "url": "http://www.google.com",
            "req": ["Google"]
        },
        {
            "url": "http://xkcd.com/1513",
            "req": ["CODE QUALITY", "xkcd"]
        },
        {
            "url": "http://xkcd.com/",
            "req": ["xkcd"]
        },
        {
            "url": "http://localhost",
            "req": []
        }
    ]
}
```

##  Usage
After installing Python, the requirements and creating the configuration file, simply run:

```bash
python harjoitus.py
```

For full list of accepted arguments, run:
```bash
python harjoitus.py -h
```

The program should execute and you should see something like the following:
```bash
> python harjoitus.py                              
 * Serving Quart app 'harjoitus'
 * Environment: production
 * Please use an ASGI server (e.g. Hypercorn) directly in production
 * Debug mode: False
 * Running on http://127.0.0.1:5000 (CTRL + C to quit)
[2022-09-27 19:44:09 +0300] [34310] [INFO] Running on http://127.0.0.1:5000 (CTRL + C to quit)
2022-09-27 19:44:09.635448 http://localhost LogStatus.CONN_NOK Cannot connect to host localhost:80 ssl:default [Connect call failed ('::1', 80, 0, 0)]
2022-09-27 19:44:09.687478 http://xkcd.com/ LogStatus.CONN_OK 
2022-09-27 19:44:09.688855 http://xkcd.com/ LogStatus.CONTENT_OK 
2022-09-27 19:44:09.975026 http://xkcd.com/1513 LogStatus.CONN_OK 
2022-09-27 19:44:09.980037 http://xkcd.com/1513 LogStatus.CONTENT_NOK CODE QUALITY not found response content
2022-09-27 19:44:10.024391 http://www.google.com LogStatus.CONN_OK 
2022-09-27 19:44:10.028120 http://www.google.com LogStatus.CONTENT_OK 
---------------
```

Open the server interface by navigating in browser to the given URL (here `http://127.0.0.1:5000`)
