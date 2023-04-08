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
    resource_url = payload['resource_url']
    response = session.get(resource_url)
    data = response.json()
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

def apply_preset_based_on_pouches(order):
    total_pouches = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in order['items']])
    preset = config.presets[str(total_pouches)]
    order['weight'] = preset['weight']
    order.update(preset)
    order['advancedOptions'].update(preset['advancedOptions'])  # Update advancedOptions separately
    return order

def set_stk_order_tag(order, need_stk_tag):
    if need_stk_tag:
        if 'customField1' not in order['advancedOptions']:
            order['advancedOptions']['customField1'] = ""

        if len(order['advancedOptions']['customField1']) == 0:
            order['advancedOptions']['customField1'] = "STK-Order"
        else:
            order['advancedOptions']['customField1'] = "STK-Order, " + order['advancedOptions']['customField1']
    return order


def process_order(order):
    need_stk_tag = any(item['sku'] == 'OTP - STK' for item in order['items'])

    if order_split_required(order):
        # Prepare the child orders and parent order
        original_order, child_orders = prepare_split_data(order)

        with ThreadPoolExecutor() as executor:
            # Update all child orders in a single request
            future1 = executor.submit(session.post, 'https://ssapi.shipstation.com/orders/createorders', data=json.dumps(child_orders))

            # Update the parent order in ShipStation
            future2 = executor.submit(session.post, 'https://ssapi.shipstation.com/orders/createorder', data=json.dumps(original_order))

            response1 = future1.result()
            response2 = future2.result()

        if response1.status_code == 200:
            print(f"Successfully created {len(child_orders)} children")
            print(f"Full success response: {response1.__dict__}")
        else:
            print(f"Unexpected status code for child orders: {response1.status_code}")
            print(f"Full error response: {response1.__dict__}")

        if response2.status_code == 200:
            print(f"Parent order created successfully")
            print(f"Full success response: {response2.__dict__}")
        else:
            print(f"Unexpected status code for parent order: {response2.status_code}")
            print(f"Full error response: {response2.__dict__}")

        return f"Successfully processed order #{order['orderNumber']}"

    else:
        order = apply_preset_based_on_pouches(order)
        order = set_stk_order_tag(order, need_stk_tag)
        response = session.post('https://ssapi.shipstation.com/orders/createorder', data=json.dumps(order))

        if response.status_code == 200:
            print(f"Order updated successfully with preset")
            print(f"Full success response: {response.__dict__}")
        else:
            print(f"Unexpected status code for updating order: {response.status_code}")
            print(f"Full error response: {response.__dict__}")

        return f"Successfully processed order #{order['orderNumber']} without splitting"


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

        child_order = prepare_child_order(original_order, child_order_items)
        child_orders.append(child_order)
        shipment_counter += 1

    total_shipments = shipment_counter

    remaining_pouches_total = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in original_order['items']])
    original_order = apply_preset_based_on_pouches(original_order)


    original_order['items'] = [item for item in original_order['items'] if item['quantity'] > 0]
    original_order['orderNumber'] = f"{original_order['orderNumber']}-1"
    original_order['advancedOptions']['customField2'] = f"Shipment 1 of {total_shipments}"

    for i in range(len(child_orders)):
        child_order = copy.deepcopy(child_orders[i])
        child_order['orderNumber'] = f"{order['orderNumber']}-{i + 2}"
        child_order['advancedOptions']['customField2'] = f"Shipment {i + 2} of {total_shipments}"
        if need_stk_tag:
            child_order = set_stk_order_tag(child_order, need_stk_tag)  # Call the new function here
            need_stk_tag = False
        child_orders[i] = child_order

    if need_stk_tag:
        original_order = set_stk_order_tag(original_order, need_stk_tag)  # Call the new function here
        need_stk_tag = False

    print(f"Parent order: {original_order}")
    print(f"Child_orders: {child_orders}")
    return original_order, child_orders


def prepare_child_order(parent_order, child_order_items):
    child_order = copy.deepcopy(parent_order)
    if 'orderId' in child_order:
        del child_order['orderId']
    child_order['items'] = child_order_items
    child_order['orderTotal'] = 0.00
    child_order['orderKey'] = str(uuid.uuid4())
    child_order['paymentDate'] = None

    remaining_pouches_total = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in child_order_items])
    child_order = apply_preset_based_on_pouches(child_order)

    # Update advanced options to reflect the relationship between the parent and child orders
    child_order['advancedOptions']['mergedOrSplit'] = True
    child_order['advancedOptions']['parentId'] = parent_order['orderId']
    parent_order['advancedOptions']['mergedOrSplit'] = True
    parent_order['advancedOptions'].pop('parentId', None)  # Remove the parentId value for the parent order
    child_order['advancedOptions']['billToParty'] = "my_other_account"
    parent_order['advancedOptions']['billToParty'] = "my_other_account"

    return child_order