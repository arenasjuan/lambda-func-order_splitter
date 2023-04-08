import base64
import uuid
import random
import json
import requests
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import config

auth_string = f"{config.SHIPSTATION_API_KEY}:{config.SHIPSTATION_API_SECRET}"
encoded_auth_string = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')


def lambda_handler(event, context):
    print("Lambda handler invoked")

    global session
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Basic {encoded_auth_string}"
    })

    # Extract webhook payload
    payload = json.loads(event["body"])
    print(payload)
    resource_url = payload['resource_url']
    print(resource_url)
    response = session.get(resource_url)
    print(response)
    data = response.json()
    print(data)
    orders = data['orders']
    print(f"Number of orders: {len(orders)}")

    '''with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(process_order, order) for order in orders]
        for future in as_completed(futures):
            future.result()'''

    for order in orders:
        process_order(order)

    return {
        'statusCode': 200,
        'body': json.dumps('Lambda function executed successfully')
    }

def order_split_required(order):
    total_pouches = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in order['items']])
    return total_pouches > 9

def process_order(order):
    if not order_split_required(order):
        return
    # Prepare the child orders and parent order
    original_order, child_orders = prepare_split_data(order)

    print(f"Process_orders original order: {original_order}")
    print(f"Process_orders child_orders: {child_orders}")

    # Create the child orders and store their orderIds
    child_order_ids = []

    # Update all child orders in a single request
    response = update_orders(child_orders)
    
    if response and response['results']:
        for created_order in response['results']:
            if created_order['success']:
                child_order_ids.append(created_order['orderId'])
                original_order['advancedOptions'].setdefault('mergedIds', [])  # Add this line
                original_order['advancedOptions']['mergedIds'].append(created_order['orderId'])

    # Update the parent order with the created child orders' orderIds
    original_order['advancedOptions']['mergedIds'] = child_order_ids

    # Update the parent order in ShipStation
    update_orders([original_order])

    return f"Successfully processed order {order['orderId']}"



def update_orders(orders):
    url = 'https://ssapi.shipstation.com/orders/createorders'

    print(f"Update-ready child orders: {orders}")
    response = session.post(url, json=orders)

    if response.status_code == 200:
        print(f"Orders updated/created successfully")
        print(f"Full response: {response.__dict__}")
        return response.json()
    else:
        print(f"Unexpected status code: {response.status_code}")
        print(f"Error updating/creating orders: {response.text}")
        print(f"Full response: {response.__dict__}")


def prepare_split_data(order):
    original_order = copy.deepcopy(order)  # Create a deep copy of the order object
    child_orders = []

    need_stk_tag = any(item['sku'] == 'OTP - STK' for item in original_order['items'])
    total_pouches = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in original_order['items']])
    
    shipment_counter = 1
    
    while total_pouches > 9:
        child_order_items = []
        child_pouches = 0

        for item in original_order['items']:
            pouches_per_item = config.sku_to_pouches.get(item['sku'], 0)
            temp_quantity = 0

            while item['quantity'] > 0 and (child_pouches + pouches_per_item) <= 9:
                item['quantity'] -= 1
                child_pouches += pouches_per_item
                temp_quantity += 1
                total_pouches -= pouches_per_item  # Update total_pouches

            if temp_quantity > 0:
                item_copy = copy.deepcopy(item)
                item_copy['quantity'] = temp_quantity
                child_order_items.append(item_copy)

        child_order = prepare_child_order(original_order, child_order_items, shipment_counter)
        child_orders.append(child_order)
        shipment_counter += 1

    total_shipments = shipment_counter

    remaining_pouches_total = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in original_order['items']])
    preset = config.presets[str(remaining_pouches_total)]
    original_order['weight']=preset['weight']
    original_order.update(preset)

    original_order['items'] = [item for item in original_order['items'] if item['quantity'] > 0]
    original_order['orderNumber'] = f"{original_order['orderNumber']}-1"
    original_order['advancedOptions']['customField2'] = f"Shipment 1 of {total_shipments}"
    print(f"Original order shipment check 1: {original_order['advancedOptions']['customField2']}")

    for i in range(len(child_orders)):
        print(need_stk_tag)
        child_order = copy.deepcopy(child_orders[i])
        print(child_order)
        child_order['orderNumber'] = f"{order['orderNumber']}-{i + 2}"
        print(f"Order number for child {i+1}: {child_order['orderNumber']}")
        child_order['advancedOptions']['customField2'] = f"Shipment {i + 2} of {total_shipments}"
        print(f"Shipment number for child {i+1}: {child_order['advancedOptions']['customField2']}")
        if need_stk_tag:
            if any(item['sku'] == 'OTP - STK' for item in child_order['items']):
                child_order['advancedOptions']['customField1'] = "STK-Order"
                print(f"STK-Order tag applied to child {i+1}")
                need_stk_tag = False
                print(f"need_stk_tag updated to {need_stk_tag}")
        child_orders[i] = child_order

    formatted_output = ', '.join([f"Child {i+1}: {child['advancedOptions']['customField2']}" for i, child in enumerate(child_orders)])
    print(f"Child shipment checks: {formatted_output}")


    print(f"Original order shipment check 2: {original_order['advancedOptions']['customField2']}")
    if need_stk_tag:
        if len(original_order['advancedOptions']['customField1']) == 0:
            original_order['advancedOptions']['customField1'] = "STK-Order"
        else:
            original_order['advancedOptions']['customField1'] = "STK-Order, " + original_order['advancedOptions']['customField1']
        need_stk_tag = False

    print(f"Original order: {original_order}")
    print(f"Child_orders: {child_orders}")
    return original_order, child_orders


def prepare_child_order(parent_order, child_order_items, shipment_counter):
    child_order = copy.deepcopy(parent_order)
    if 'orderId' in child_order:
        del child_order['orderId']
    child_order['items'] = child_order_items
    child_order['orderTotal'] = 0.00
    child_order['orderKey'] = str(uuid.uuid4())
    child_order['paymentDate'] = None

    remaining_pouches_total = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in child_order_items])
    preset = config.presets[str(remaining_pouches_total)]
    child_order['weight']=preset['weight']
    child_order.update(preset)
    print(preset)
    child_order['advancedOptions'].update(preset['advancedOptions'])  # Update advancedOptions separately

    # Update advanced options to reflect the relationship between the parent and child orders
    child_order['advancedOptions']['mergedOrSplit'] = True
    child_order['advancedOptions']['parentId'] = parent_order['orderId']
    parent_order['advancedOptions']['mergedOrSplit'] = True
    parent_order['advancedOptions'].pop('parentId', None)  # Remove the parentId value for the parent order
    child_order['advancedOptions']['billToParty'] = "my_other_account"
    parent_order['advancedOptions']['billToParty'] = "my_other_account"

    print(f"Child order after prepare_child_order: {child_order}")
    return child_order
