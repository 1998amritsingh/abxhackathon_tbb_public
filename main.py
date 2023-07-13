import openai
import os
import sys
import json
from venmo_api import Client, PaymentPrivacy
import gradio as gr
from file_detect import file_detect_ocr

OPENAI_API_KEY = ""
VENMO_USERNAME = ""
VENMO_PASSWORD = ""
openai.api_key = OPENAI_API_KEY

def get_completion_from_messages(messages, model="gpt-3.5-turbo", temperature=0):
    response = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=temperature, # this is the degree of randomness of the model's output
    )
    return response.choices[0].message["content"]

def structure_digitzed_input(raw_digized_input):
    pre_processing_prompt = "You will receive a piece of OCR-generated text called {ItemizedReceipt}, which will represent each item on the bill and its corresponding subtotal. \
    {ItemizedReceipt} will also include any taxes or tips paid, if applicable. \
    Convert {ItemizedReceipt} into JSON using the following example, where the JSON keys of the generated output match what is present in the example: \
{ \
  'items': [ \
    { \
      'name': <name>, \
      'price': <price> \
    }, \
    { \
      'name': <name>, \
      'price': <price> \
    }, \
    { \
      'name': <name>, \
      'price': <price> \
    }, \
    { \
      'name': <name>, \
      'price': <price> \
    }, \
    { \
      'name': <name>, \
      'price': <price> \
    }, \
  ], \
  'subtotal': <subtotal>, \
  'tax': <tax>, \
  'tip': <tip>, \
  'total': <total> \
} \
Output only the final JSON. \
ItemizedReceipt: <itemized_receipt> \
Output: "

    # Replace the <itemized_receipt> placeholder with the actual input data
    pre_processing_prompt = pre_processing_prompt.replace("<itemized_receipt>", raw_digized_input)

    # Call the LLM and return the completion
    context = [{'role': 'user', 'content': f"{pre_processing_prompt}"}]
    return get_completion_from_messages(context.copy(), temperature=0)

def gen_individual_cost_breakdown(itemized_receipt_json, item_breakdown):
    cost_breakdown_prompt = "\
    You are an AI assistant designed to generate cost breakdowns given a receipt and a description of what each person involved in the transaction ordered. \
    You will receive a JSON object called {ItemizedReceiptJSON} that provides a breakdown of the individual items ordered and their corresponding price. \
    {ItemizedReceiptJSON} may also include a tax, tip, and total. \
    Additionally, you will receive a piece of CSV input called {ItemBreakdown}. \
    This will include the name of a person involved in the transaction, their venmo id and what items that person is responsible for. \
    The headers for this CSV are {name}, {venmo_usernames}, {items} . \
    Output a JSON in the same format as {ItemizedReceiptJSON}, except with an additional key for each 'item' called 'split'. \
    'split' will contain the names of each person under it as sub-keys, with the values for those corresponding sub keys as the boolean value true if they were involved for that item, false if they were not. \
    also add a key 'venmo' at the root of the json which takes names as key from {name} field and values from the {venmo_usernames} field in the {ItemBreakdown} CSV file.\
    ItemizedReceiptJSON: <itemized_receipt_json> \
    ItemBreakdown: <item_breakdown> \
    "

    # Replace the <itemized_receipt_json> and <item_breakdown> placeholders with the actual input data
    cost_breakdown_prompt = cost_breakdown_prompt.replace("<itemized_receipt_json>", itemized_receipt_json)
    cost_breakdown_prompt = cost_breakdown_prompt.replace("<item_breakdown>", item_breakdown)

    # Call the LLM and return the completion.
    context = [{'role': 'user', 'content': f"{cost_breakdown_prompt}"}]
    cost_breakdown_json = json.loads(get_completion_from_messages(context.copy(), temperature=0))

    # Parse the cost breakdown JSON to generate a dictionary with each person's name as a key and their individual bill as the value
    cost_breakdown_dict = {}
    for item in cost_breakdown_json['items']:
        # Compute each person's sub total for the individual item
        price = float(item['price'].replace("$", ""))
        split = item['split']
    
        # compute the number of people involved in the transaction in order to determine how to split the cost of the item
        divisor = 0
        for name, involved in split.items():
            if involved:
                divisor += 1

        # Now compute each person's subtotal
        for name, involved in split.items():
            # Should never be 0 but this is a safety check
            if divisor == 0:
                continue
            # skip those who were not involved in the transaction
            if not involved:
                continue
            individual_cost = price/divisor
            if name not in cost_breakdown_dict:
                cost_breakdown_dict[name] = individual_cost
            else:
                cost_breakdown_dict[name] += individual_cost

    # add tax and tip split evenly to each person's total
    num_people = len(cost_breakdown_dict)
    tax_plus_tip_indiv = (float(cost_breakdown_json['tax'].replace("$", "")) + float(cost_breakdown_json['tip'].replace("$", "")))/(num_people)

    # go through dict again and add tax_plus_tip_indiv to each key
    for k in cost_breakdown_dict:
        cost_breakdown_dict[k] += tax_plus_tip_indiv
    return cost_breakdown_dict, cost_breakdown_json['venmo']

def read_file_content(file_path, file):
    if file is None:
      file = open(file_path, 'r')
    
    content = file.read()    
    return content

def create_summary(cost_breakdown_dict, item_breakdown):
    meal_summary_prompt = "\
    You are an AI assistant designed to generate a summary of the meal breakdown. \
    You will receive a dictionary called {CostBreakdownDict} that contains the total amount each individual paid for that meal. \
    Additionally, you will receive a piece of CSV input called {ItemBreakdown}, this will include the name of the person, their venmo id and the item's they had for the meal.\
    The headers for this CSV are {name}, {venmo_username}, {items}. The values in the {items} field are separated by semicolon as a delimeter\
    Output a short summary of the meal in a human readable format. Also add a sarcastic , dire and funny spin to it.\
    CostBreakdownDict: <cost_breakdown_dict> \
    ItemBreakdown: <item_breakdown> \
    "

    # Replace the <itemized_receipt_json> and <item_breakdown> placeholders with the actual input data
    meal_summary_prompt = meal_summary_prompt.replace("<cost_breakdown_dict>", json.dumps(cost_breakdown_dict))
    meal_summary_prompt = meal_summary_prompt.replace("<item_breakdown>", item_breakdown)

    # Call the LLM and return the completion.
    context = [{'role': 'user', 'content': f"{meal_summary_prompt}"}]
    summary = get_completion_from_messages(context.copy(), temperature=0)
    return summary

def create_nutrition_summary(item_breakdown):
    meal_summary_prompt = "\
    You are an AI assistant designed to generate a detailed summary of the meal's nutrition per person. Include the approximate values of the nutrition. Also add a suggestion at the end.\
    You will receive a piece of CSV input called {ItemBreakdown}, this will include the name of the person and the item they had for the meal. \
    The headers for this CSV are {name}, {venmo_username}, {items}. \
    ItemBreakdown: <item_breakdown> \
    "

    # Replace the <item_breakdown> placeholders with the actual input data
    meal_summary_prompt = meal_summary_prompt.replace("<item_breakdown>", item_breakdown)

    # Call the LLM and return the completion.
    context = [{'role': 'user', 'content': f"{meal_summary_prompt}"}]
    nutrition_summary = get_completion_from_messages(context.copy(), temperature=0)
    return nutrition_summary


def init_venmo_client(username, password):  
    # Get your access token. You will need to complete the 2FA process
    access_token = Client.get_access_token(username,
                                        password)
    venmo = Client(access_token=access_token)
    return venmo

def venmo_payment_request(client:Client, username, amount, description):
    user = client.user.get_user_by_username(username)
    return client.payment.request_money(amount=amount, note=description, privacy_setting=PaymentPrivacy.PRIVATE, target_user_id=user.id)

def process_input(person1, item1, venmo_id1, person2, item2, venmo_id2, person3, item3, venmo_id3, receipt_filepath):
    itemized_receipt = file_detect_ocr(receipt_filepath)

    item1 = str(item1).replace(',', ';')
    item2 = str(item2).replace(',', ';')
    item3 = str(item3).replace(',', ';')
    
    # generate a string in CSV format with the headers `name`, `item` and `venmo_username` using the input parameters.
    item_breakdown = "name,venmo_username,items\n"
    item_breakdown += (person1 + "," + venmo_id1 + "," + item1 + "\n")
    item_breakdown += (person2 + ","  + venmo_id2 +  ","  + item2 + "\n")
    item_breakdown += (person3 + "," + venmo_id3 + "," + item3)

    # Convert unstructured itemized receipt into JSON
    itemized_receipt_json = structure_digitzed_input(itemized_receipt)

    # Generate a dictionary with the cost breakdowns for individuals
    cost_breakdown_dict, venmo_usernames = gen_individual_cost_breakdown(itemized_receipt_json, item_breakdown)
    
    summary = create_summary(cost_breakdown_dict, item_breakdown)
    nutrition_summary = create_nutrition_summary(item_breakdown)

    venmo_client = init_venmo_client(username=VENMO_USERNAME, password=VENMO_PASSWORD)
    myprofile = venmo_client.user.get_my_profile().username
    for key in venmo_usernames.keys():
      if myprofile == venmo_usernames[key]:
          continue
      else:
          venmo_payment_request(venmo_client, venmo_usernames[key], cost_breakdown_dict[key], "Meal - Hackathon Charges. Whether to accept this request or not, I leave that upto you.")    
    return summary,nutrition_summary


def main():
    with gr.Blocks() as demo:
        person1 = gr.Textbox(label="Person 1")
        item1 = gr.Textbox(label="Items")
        venmo_id1 = gr.Textbox(label="Venmo ID 1")
        person2 = gr.Textbox(label="Person 2")
        item2 = gr.Textbox(label="Items")
        venmo_id2 = gr.Textbox(label="Venmo ID 2")
        person3 = gr.Textbox(label="Person 3")
        item3 = gr.Textbox(label="Items")
        venmo_id3 = gr.Textbox(label="Venmo ID 3")

        receipt_filepath = gr.Textbox(label="Receipt Filepath")

        breakdown = gr.Textbox(label="Breakdown Summary")
        nutrition_summary = gr.Textbox(label="Nutrition Summary")
        greet_btn = gr.Button("Calculate")
        greet_btn.click(fn=process_input, inputs=[person1, item1, venmo_id1, person2, item2, venmo_id2, person3, item3, venmo_id3, receipt_filepath], outputs=[breakdown,nutrition_summary], api_name="process_input")
    demo.launch()

if __name__ == '__main__':
    main()