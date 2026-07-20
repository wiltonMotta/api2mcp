# Build a Python MCP Server
## name
MCP reverse proxying API With Document
## tech stacks:
use fastmcp
database use sqlite

## database structure:
### table APIs
name: string (key)
document: string
#### document field instruction
the document field is a stringify json, the schema will be like this example:
```JSON
{
    url: "https://jsonplaceholder.typicode.com/todos/:id",
    method: 'GET',
    description: "Get one user's todo list by todo item's ID",
    parameters: {
        format: "URLParameter",
        schema: {
            id: {
                type: "integer",
                description: "todo item's ID",
                optional: false
            }    
        }
    },
    returns: {
        format: 'JSON',
        schema: {
            userId: {
                type: "integer",
                description: "User's ID",
                optional: false
            },
            id: {
                type: "integer",
                description: "Todo item's ID",
                optional: false
            },
            title: {
                type: "string",
                description: "Todo item's title",
                optional: true
            },
            completed:{
                type: "boolean",
                description: "Whether the item is marked complete",
                optional: false
            }
        }
    }
}
```
the schema describe a HTTP API endpoint.

## functions:
A MCP server with dynamic MCP endpoint by name which read from database by APIs' name
when calling a MCP, a database item will be loaded, parse the URL then send a request to the URL as the JSON defined the return the result via MCP. Also use the parameters/returns to fill the MCP description for parameters and returns (for agents using the MCP to read and understand the meaning of the API and parameters/returns).


## Generate
code in main.py
requirements.txt for pip to install the libs
sql file to creating the database
an example database 
