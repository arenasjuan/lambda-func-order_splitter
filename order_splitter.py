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

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(process_order, order) for order in orders]
        for future in as_completed(futures):
            future.result()

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

    # Create the child orders and store their orderIds
    child_order_ids = []

    # Update all child orders in a single request
    response = update_orders(child_orders)
    
    if response and response['results']:
        for created_order in response['results']:
            if created_order['success']:
                child_order_ids.append(created_order['orderId'])
                original_order['advancedOptions']['mergedIds'].append(created_order['orderId'])

    # Update the parent order with the created child orders' orderIds
    original_order['advancedOptions']['mergedIds'] = child_order_ids

    # Update the parent order in ShipStation
    update_orders([original_order])

    return f"Successfully processed order {order['orderId']}"


def update_orders(orders):
    url = 'https://ssapi.shipstation.com/orders/createorders'

    response = session.post(url, json=orders)

    if response.status_code == 200:
        print(f"Orders updated/created successfully")
        print(f"Full response: {response.__dict__}")
    else:
        print(f"Unexpected status code: {response.status_code}")
        print(f"Error updating/creating orders: {response.text}")
        print(f"Full response: {response.__dict__}")




def prepare_split_data(order):
    original_order = order.copy()
    child_orders = []
    remaining_pouches = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in order['items']])
    
    shipment_counter = 1
    
    while remaining_pouches > 9:
        child_order_items = []
        child_pouches = 0

        for item in order['items']:
            pouches_per_item = config.sku_to_pouches.get(item['sku'], 0)
            while item['quantity'] > 0 and (child_pouches + pouches_per_item) <= 9:
                item['quantity'] -= 1
                remaining_pouches -= pouches_per_item
                child_pouches += pouches_per_item

                item_copy = copy.deepcopy(item)
                item_copy['quantity'] = 1
                child_order_items.append(item_copy)

        child_order = prepare_child_order(order, child_order_items, shipment_counter)
        child_orders.append(child_order)
        shipment_counter += 1

    total_shipments = shipment_counter

    # Keep all original item data while updating the original order, and remove items with a quantity of 0
    original_order['items'] = [item for item in order['items'] if item['quantity'] > 0]
    original_order['orderNumber'] = f"{original_order['orderNumber']}-1"
    original_order['advancedOptions']['customField2'] = f"Shipment 1 of {total_shipments}"

    for i, child_order in enumerate(child_orders):
        child_order['orderNumber'] = f"{order['orderNumber']}-{i + 2}"
        child_order['advancedOptions']['customField2'] = f"Shipment {i + 2} of {total_shipments}"

    return original_order, child_orders


def prepare_child_order(parent_order, child_order_items, shipment_counter):
    child_order = copy.deepcopy(parent_order)
    if 'orderId' in child_order:
        del child_order['orderId']
    child_order['items'] = child_order_items
    child_order['orderTotal'] = 0.00
    child_order['orderKey'] = str(uuid.uuid4())
    child_order['paymentDate'] = None
    child_order['orderNumber'] = f"{parent_order['orderNumber']}-{shipment_counter + 1}"
    child_order['advancedOptions']['customField2'] = f"Shipment {shipment_counter + 1} of {shipment_counter}"

    # Calculate the weight of the child order
    child_order_weight = sum([item['weight']['value'] for item in child_order_items])
    child_order['weight']['value'] = child_order_weight

    # Update advanced options to reflect the relationship between the parent and child orders
    child_order['advancedOptions']['mergedOrSplit'] = True
    child_order['advancedOptions']['parentId'] = parent_order['orderId']
    parent_order['advancedOptions']['mergedOrSplit'] = True
    parent_order['advancedOptions'].pop('parentId', None)  # Remove the parentId value for the parent order
    child_order['advancedOptions']['billToParty'] = "my_other_account"
    parent_order['advancedOptions']['billToParty'] = "my_other_account"
    child_order['advancedOptions']['billToMyOtherAccount'] = 647173
    parent_order['advancedOptions']['billToMyOtherAccount'] = 647173


    return child_order