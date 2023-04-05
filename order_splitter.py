import base64
import json
import requests
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
    # Modify the resource_url to includeShipmentItems=True to access the order items
    response = session.get(payload['resource_url'][:-5] + "True")
    data = response.json()
    shipments = data['orders']
    print(f"Number of shipments: {len(shipments)}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(process_order, shipment) for shipment in shipments]
        for future in as_completed(futures):
            future.result()

    return {
        'statusCode': 200,
        'body': json.dumps('Lambda function executed successfully')
    }

def process_order(shipment):
    order = shipment  # Extract order information from the shipment

    if order_split_required(order):
        original_order, child_orders = prepare_split_data(order)

        # Update the original order with reduced quantities
        update_original_order(original_order)

        # Create new child orders
        for child_order in child_orders:
            create_child_order(child_order)

    # Format order items list as a string
    order_items = ', '.join([f"{item['sku']}({item['quantity']})" for item in order['items']])

    # Add the formatted order items string to the order object
    order['customerNotes'] = f"Items: {order_items}"

def update_original_order(order_data):
    url = 'https://ssapi.shipstation.com/orders/createorder'
    response = session.post(url, json=order_data)

    if response.status_code == 200:
        print(f"Original order {order_data['orderId']} updated successfully")
    else:
        print(f"Error updating original order {order_data['orderId']}: {response.text}")

def create_child_order(child_order):
    url = 'https://ssapi.shipstation.com/orders/createorder'
    response = session.post(url, json=child_order)

    if response.status_code == 200:
        print("Child order created successfully")
    else:
        print(f"Error creating child order: {response.text}")

def order_split_required(order):
    total_pouches = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in order['items']])
    return total_pouches > 9

def prepare_split_data(order):
    original_order = order.copy()
    child_orders = []
    remaining_pouches = sum([item['quantity'] * config.sku_to_pouches.get(item['sku'], 0) for item in order['items']])

    while remaining_pouches > 9:
        child_order = order.copy()
        child_order_items = []

        for item in order['items']:
            pouches_per_item = config.sku_to_pouches.get(item['sku'], 0)
            while item['quantity'] > 0 and remaining_pouches > 9:
                item['quantity'] -= 1
                remaining_pouches -= pouches_per_item
                child_order_items.append({'sku': item['sku'], 'quantity': 1})

        child_order['items'] = child_order_items
        child_orders.append(child_order)

    original_order['items'] = [{'sku': item['sku'], 'quantity': item['quantity']} for item in order['items']]

    return original_order, child_orders
