import requests
import asyncio
import aiohttp
from datetime import datetime
from flask import Flask, render_template


app = Flask(__name__)

# todo:
# caching responses and using last-modified header
# adding polling or streaming 
# use API key (currently rate limited after a few refresh)
# currenlty only for north station

@app.route('/', methods=['GET'])
def departures():
    mbta = 'https://api-v3.mbta.com'
    stop = 'place-north'
    headers = {'Accept-Encoding': 'application/gzip'}
    api_routes = f'{mbta}/routes'
    routes_param = {
        'filter[type]': 2,
        'filter[stop]': stop,
        'fields[route]': 'id',
    }

    routes = requests.get(
        url=api_routes, 
        params=routes_param,
        headers=headers
    )

    api_predictions = f'{mbta}/predictions'
    predictions_params = []
    departures = []
    formatted_time = {}
    if routes.status_code == 200:
        for route in routes.json()['data']:
            predictions_param = {
                'filter[stop]': stop,
                'fields[prediction]': 'departure_time,direction_id,status',
                'include': 'stop,schedule,trip',
            }
            predictions_param['filter[route]'] = route['id']
            predictions_params.append(predictions_param)

        gathered = asyncio.run(get_predictions(
            api_predictions, 
            predictions_params, 
            headers
        ))

        departures, current_time = get_data(gathered)
        formatted_time = make_time_dict(current_time)
    
    return render_template(
        'departures.html',
        title="North Station MBTA Departure Board",
        departures=departures,
        current_time=formatted_time
    )

async def fetch(session, url, params, headers):
    async with session.request('GET', url, params=params, headers=headers) as resp:
        body = await resp.json()
        last_modified = resp.headers['last-modified']
    body.update({'last_modified': last_modified}) 

    return body

async def get_predictions(url, predictions_params, headers):
    tasks = []
    async with aiohttp.ClientSession() as session:
        for params in predictions_params:
            task = asyncio.create_task(fetch(session, url, params, headers))
            tasks.append(task)

        return await asyncio.gather(*tasks)

def get_data(gathered):
    included = {}
    predictions = []
    current_time = None
    for data in gathered:
        if data['data']:
            for inc in data['included']:
                att = inc['attributes']
                if inc['type'] == 'stop':
                    included[inc['id']] = att['platform_code']
                elif inc['type'] == 'schedule':
                    included[inc['id']] = att['departure_time']
                    if not current_time:
                        tz = datetime.fromisoformat(att['departure_time']).tzinfo
                        current_time = datetime.now(tz)
                elif inc['type'] == 'trip':
                    included[inc['id']] = {
                        'headsign': att['headsign'],
                        'name': att['name']
                    }

            prediction = choose_prediction(data['data'], included, current_time)
            if prediction:
                predictions.append(prediction)

    return (predictions, current_time)

def choose_prediction(predictions, included, current_time):
    chosen = None
    for pre in predictions:
        att = pre['attributes']
        if att['direction_id'] == 0:
            if att['status'] == 'Departed':
                schedule_id = pre['relationships']['schedule']['data']['id']
                departed_time = datetime.fromisoformat(included[schedule_id])
                if (current_time - departed_time).seconds < 300:
                    chosen = pre
            else:
                chosen = chosen or pre

    if chosen:
        return transform_prediction(chosen, included)

def transform_prediction(prediction, included):
    att = prediction['attributes']

    schedule_id = prediction['relationships']['schedule']['data']['id']
    departure_time = att['departure_time'] or included[schedule_id]
    att['departure_time'] = datetime.fromisoformat(departure_time).strftime('%-I:%M %p')

    trip_id = prediction['relationships']['trip']['data']['id']
    att['destination'] = included[trip_id]['headsign']
    att['train_no'] = included[trip_id]['name'] 

    stop_id = prediction['relationships']['stop']['data']['id']
    att['track_no'] = included[stop_id] or 'tbd'

    att['status'] = att['status']
        
    return att

def make_time_dict(current_time):
    return {
        'day': current_time.strftime('%A'),
        'date': current_time.strftime('%-m-%-d-%Y'),
        'hour': current_time.strftime('%-I:%M %p'),
    }



if __name__ == '__main__':
    app.run()